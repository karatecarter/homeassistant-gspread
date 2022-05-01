from functools import partial

import voluptuous as vol
import logging
from aiohttp import ClientError
import gspread
#import pandas as pd
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import sys
from datetime import timedelta
from typing import Any, Callable, Dict, Optional
DEPENDENCIES = ('webhook',)

from aiohttp import hdrs
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from homeassistant.components.sensor import PLATFORM_SCHEMA
from homeassistant.const import (
    ATTR_NAME,
    CONF_NAME,
    CONF_PATH
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.typing import (
    ConfigType,
    DiscoveryInfoType,
    HomeAssistantType,
)
from .const import (
DOMAIN
)

_LOGGER = logging.getLogger(__name__)
# Time between updating data from Google
SCAN_INTERVAL = timedelta(minutes=1)

CONF_API_KEY = "api_key"
CONF_API_KEY_SCHEMA = vol.Schema(
    {vol.Required("type"): cv.string,
     vol.Required("project_id"): cv.string,
     vol.Required("private_key_id"): cv.string,
     vol.Required("private_key"): cv.string,
     vol.Required("client_email"): cv.string,
     vol.Required("client_id"): cv.string,
     vol.Required("auth_uri"): cv.string,
     vol.Required("token_uri"): cv.string,
     vol.Required("auth_provider_x509_cert_url"): cv.string,
     vol.Required("client_x509_cert_url"): cv.string}
)

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required("sheet_name"): cv.string,
        vol.Required(CONF_API_KEY): CONF_API_KEY_SCHEMA
    }
)



def setup_platform(
    hass: HomeAssistantType,
    config: ConfigType,
    add_entities: Callable,
    discovery_info: Optional[DiscoveryInfoType] = None,
) -> None:
    scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
    api_key_dict = config[CONF_API_KEY]
    creds = ServiceAccountCredentials.from_json_keyfile_dict(api_key_dict, scope)

    sensors = [GspreadSensor(creds, config["sheet_name"])]
    add_entities(sensors, update_before_add=True)

    def log_to_gspread(call):
        sheet_name = call.data.get("sheet_name")
        date = call.data.get("date")
        period = call.data.get("period", "morning")
        amount = call.data.get("amount", 0)

        if period == "morning":
            period = "AM"
            cell = "B"
        else:
            period = "PM"
            cell = "C"

        client = gspread.authorize(creds)
        sheet = client.open(sheet_name)
        sheet_instance = sheet.get_worksheet(0)

        records_data = sheet_instance.get_all_records()

        insert_row = True

        i = 1
        for record in records_data:
            if insert_row:
                i = i + 1
                if record.get("Date") == date:
                    insert_row = False

        if insert_row:
            sheet_instance.append_row([date])
            i = i + 1

        sheet_instance.update(cell + str(i), amount)

    hass.services.register(DOMAIN, "log", log_to_gspread)


class GspreadSensor(Entity):
    """Representation of a Google Sheet sensor."""

    def __init__(self, creds: ServiceAccountCredentials, sheet_name: str):
        super().__init__()
        self._creds = creds

        client = gspread.authorize(self._creds)
        sheet = client.open(sheet_name)

        self._name = sheet_name
        self._state = None
        self._available = True
        self._sheet_id = sheet.id

        self._attrs: Dict[str, Any] = {}

    @property
    def name(self) -> str:
        """Return the name of the entity."""
        return self._name

    @property
    def unique_id(self) -> str:
        """Return the unique ID of the sensor."""
        return self._sheet_id

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._available

    @property
    def state(self) -> Optional[str]:
        return self._state

    @property
    def extra_state_attributes(self) -> Dict[str, Any]:
        return self._attrs

    def update(self):
        try:
            client = gspread.authorize(self._creds)
            sheet = client.open(self._name)
            sheet_instance = sheet.get_worksheet(0)
            all_records = sheet_instance.get_all_records()
            self._attrs["content"] = all_records
            if len(all_records) > 0:
                last_record = all_records.pop()
                self._state = last_record['Date']
            else:
                self._state = ""
            self._available = True
        except (ClientError, Exception):
            self._available = False
            _LOGGER.exception("Error retrieving data from Google Sheet.")