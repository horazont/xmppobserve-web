import logging

import quart.flask_patch
import quart.logging

from quart import (
    Quart, render_template,
    has_request_context, request,
)


class RequestFormatter(logging.Formatter):
    def format(self, record):
        if has_request_context():
            record.request_id = getattr(request, "xmppobserve_id", "-")
        else:
            record.request_id = "-"
        return super().format(record)


app = Quart(__name__)
app.config.from_envvar("XMPPOBSERVE_WEB_CONFIG")


@app.route("/", methods=["GET", "POST"])
async def index():
    return await render_template("index.html")


@app.route("/LICENSE", methods=["GET"])
async def license():
    return await render_template("license.html")


from . import api  # NOQA
app.register_blueprint(api.v1)
api.ip_rate_limiter.init_app(app)
api.target_rate_limiter.init_app(app)
api.global_rate_limiter.init_app(app)
