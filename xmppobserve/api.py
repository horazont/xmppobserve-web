import base64
import ipaddress
import json
import logging
import urllib.parse
import random
import time

from datetime import datetime

import aiohttp

import prometheus_client.parser

import quart.flask_patch

from quart import (
    Blueprint, current_app, request, abort, Response,
    has_request_context,
)

from . import ratelimit


logger = logging.getLogger(__name__)
v1 = Blueprint("api", __name__, url_prefix="/api/v1")


@v1.before_request
def assign_request_id():
    # not using a secure source of random here, since this is merely for
    # debugging purposes and runs on every request.
    request.xmppobserve_id = base64.urlsafe_b64encode(
        random.getrandbits(12*8).to_bytes(12, "little")
    ).decode("ascii")


def get_request_id():
    if not has_request_context():
        return "-"
    else:
        return "{}@{}".format(
            getattr(request, "xmppobserve_id", "-"),
            request.remote_addr,
        )


@v1.errorhandler(404)
@v1.errorhandler(400)
@v1.errorhandler(405)
def _handle_api_error(exc):
    return Response(json.dumps({
        "request_id": get_request_id(),
        "message": str(exc),
        "type": "modify",
    }), exc.status_code, content_type="application/json")


@v1.errorhandler(401)
@v1.errorhandler(403)
def _handle_api_error(exc):
    return Response(json.dumps({
        "request_id": get_request_id(),
        "message": str(exc),
        "type": "auth",
    }), exc.status_code, content_type="application/json")


@v1.errorhandler(429)
@v1.errorhandler(502)
def _handle_api_error(exc):
    return Response(json.dumps({
        "request_id": get_request_id(),
        "message": str(exc),
        "type": "wait",
    }), exc.status_code, content_type="application/json")


@v1.errorhandler(500)
@v1.errorhandler(Exception)
def _handle_api_error(exc):
    logger.exception("failed to process request %s",
                     get_request_id(),
                     exc_info=exc)
    return Response(json.dumps({
        "request_id": get_request_id(),
        "message": "INTERNAL_SERVER_ERRORError(500)",
        "type": "cancel",
    }), 500, content_type="application/json")


def _handle_api_error(exc):
    logger.exception("failed to process request %s",
                     get_request_id(),
                     exc_info=exc)
    return Response(json.dumps({
        "request_id": get_request_id(),
        "message": "INTERNAL_SERVER_ERRORError(500)",
        "type": "cancel",
    }), 500, content_type="application/json")


@ratelimit.rate_limiter_plugin("IP_RATE_LIMIT")
def ip_rate_limiter():
    addr = ipaddress.ip_address(request.remote_addr)
    if getattr(addr, "ipv4_mapped", None) is not None:
        addr = addr.ipv4_mapped
    if addr.version == 4:
        net = ipaddress.ip_network(addr).supernet(8)
    else:
        net = ipaddress.ip_network(addr).supernet(72)
    return net.network_address.packed


@ratelimit.rate_limiter_plugin("TARGET_RATE_LIMIT")
async def target_rate_limiter():
    target = (await request.json)["target"]
    return target


@ratelimit.rate_limiter_plugin("GLOBAL_RATE_LIMIT", fixed_buckets=1)
def global_rate_limiter():
    return 0


def _samples(metrics):
    for metric in metrics:
        for sample in metric.samples:
            yield sample


def smokecheck_target(target):
    url = urllib.parse.urlparse(target)
    if url.scheme != "" or url.path != target:
        raise ValueError("invalid target: {}".format(target))


async def call_prober(module, target):
    ep = random.choice(current_app.config["PROBER_ENDPOINTS"])

    async with aiohttp.ClientSession() as session:
        t0 = time.monotonic()
        try:
            async with session.get(
                    ep,
                    params={"module": module, "target": target}) as resp:
                if resp.status != 200:
                    return abort(500)
                body = (await resp.read()).decode("ascii")
                t1 = time.monotonic()
        except aiohttp.ClientConnectorError as exc:
            return abort(502)

    metrics = prometheus_client.parser.text_string_to_metric_families(body)

    return t1-t0, metrics


async def simple_probe(module, target):
    req_duration, metrics = await call_prober(module, target)

    result = {
        "request_id": get_request_id(),
        "success": None,
        "durations": {},
        "total_duration": req_duration,
        "certificate_expiration": None,
        "auth_info": {
            "dialback_offered": False,
            "sasl_mechanisms": [],
        },
    }

    for sample in _samples(metrics):
        if sample.name == "probe_success":
            result["success"] = sample.value > 0.5
        elif sample.name == "probe_xmpp_duration_seconds":
            result["durations"][sample.labels["phase"]] = sample.value
        elif sample.name == "probe_ssl_earliest_cert_expiry":
            result["certificate_expiration"] = "{:%Y-%m-%dT%H:%M:%SZ}".format(
                datetime.utcfromtimestamp(sample.value)
            )
        elif sample.name == "probe_dialback_offered":
            result["auth_info"]["dialback_offered"] = sample.value > 0.5
        elif sample.name == "probe_sasl_mechanism_offered":
            mech = sample.labels["mechanism"]
            if sample.value > 0.5:
                result["auth_info"]["sasl_mechanisms"].append(mech)

    return result


async def full_probe(module_config_key, ratelimit_pay_func):
    target = (await request.json)["target"].strip()
    smokecheck_target(target)
    target_url = "xmpp:{}".format(target)

    module = current_app.config[module_config_key]

    if not ratelimit_pay_func():
        # the preflight check passed, but the actual payment did not. this
        # can be caused by a race condition.
        return abort(429)

    return await simple_probe(module, target_url)


@v1.route("/check/xmpp-server", methods=["POST"])
@ratelimit.multi_rate_limit(ip_rate_limiter, target_rate_limiter,
                            global_rate_limiter)
async def v1_check_s2s_normal(ratelimit_pay_func):
    return await full_probe("PROBER_XMPP_SERVER_MODULE", ratelimit_pay_func)


@v1.route("/check/xmpps-server", methods=["POST"])
@ratelimit.multi_rate_limit(ip_rate_limiter, target_rate_limiter,
                            global_rate_limiter)
async def v1_check_s2s_direct(ratelimit_pay_func):
    return await full_probe("PROBER_XMPPS_SERVER_MODULE", ratelimit_pay_func)


@v1.route("/check/xmpp-client", methods=["POST"])
@ratelimit.multi_rate_limit(ip_rate_limiter, target_rate_limiter,
                            global_rate_limiter)
async def v1_check_c2s_normal(ratelimit_pay_func):
    return await full_probe("PROBER_XMPP_CLIENT_MODULE", ratelimit_pay_func)


@v1.route("/check/xmpps-client", methods=["POST"])
@ratelimit.multi_rate_limit(ip_rate_limiter, target_rate_limiter,
                            global_rate_limiter)
async def v1_check_c2s_direct(ratelimit_pay_func):
    return await full_probe("PROBER_XMPPS_CLIENT_MODULE", ratelimit_pay_func)
