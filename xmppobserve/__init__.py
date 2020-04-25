import quart.flask_patch

from quart import (
    Quart, render_template,
)

app = Quart(__name__)

@app.route("/", methods=["GET", "POST"])
async def index():
    return await render_template("index.html")
