from slack_bolt import App

from .digest_command import digest_command_callback


def register(app: App):
    app.command("/digest")(digest_command_callback)
