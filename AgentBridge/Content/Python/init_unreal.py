"""Unreal runs this file at editor startup because it sits in a plugin's Content/Python."""

import agent_bridge

agent_bridge.start()
