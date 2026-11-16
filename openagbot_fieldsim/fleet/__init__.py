"""Vloot: echte robots live volgen in de wereld van de sim en er commando's naartoe sturen.

  model.py     RobotConfig (wat je bewaart) en Telemetry (wat een robot nu meldt)
  adapters.py  per soort robot een adapter die zijn API vertaalt; nieuwe soorten registreer je met `register`
  manager.py   FleetManager: de lijst met robots (fleet.json), per robot een verbinding die blijft meten

De sim zelf blijft de sim-robot rijden; de vloot staat er los naast en leest alleen, behalve bij een commando
dat de gebruiker zelf geeft (stop, missie starten, kaart sturen).
"""
from openagbot_fieldsim.fleet.adapters import ADAPTERS, RobotAdapter, RobotError, register
from openagbot_fieldsim.fleet.manager import FleetManager
from openagbot_fieldsim.fleet.model import RobotConfig, Telemetry

__all__ = ["ADAPTERS", "FleetManager", "RobotAdapter", "RobotConfig", "RobotError", "Telemetry", "register"]
