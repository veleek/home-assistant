"""
Windows Push Notification Services (WNS) platform for notify component.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/notify.windows/
"""
import logging
from datetime import datetime, timedelta
import requests
import voluptuous as vol
from voluptuous.humanize import humanize_error

from homeassistant.util.json import load_json, save_json
import homeassistant.helpers.config_validation as cv
from homeassistant.exceptions import HomeAssistantError
# from homeassistant.components.frontend import add_manifest_json_key
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.notify import (
    ATTR_TITLE, ATTR_TITLE_DEFAULT, ATTR_TARGET, ATTR_DATA, PLATFORM_SCHEMA,
    BaseNotificationService)
from homeassistant.const import (
    HTTP_BAD_REQUEST, HTTP_INTERNAL_SERVER_ERROR)

_LOGGER = logging.getLogger(__name__)
DEPENDENCIES = ['frontend']

REGISTRATIONS_FILE = 'windows_push_registrations.conf'

CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend({
    vol.Optional(CONF_CLIENT_ID): cv.string,
    vol.Optional(CONF_CLIENT_SECRET): cv.string,
})

ATTR_CHANNEL_ID = "id"
ATTR_CHANNEL = "channel"
ATTR_EXPIRY = "expiry"
ATTR_NAME = "name"

ATTR_DATA_LAUNCH = "launch"
ATTR_DATA_HERO = "hero"
ATTR_DATA_LOGO = "logo"

SUBSCRIPTION_SCHEMA = vol.All(
    dict, vol.Schema({
        # pylint: disable=no-value-for-parameter
        vol.Required(ATTR_CHANNEL_ID): cv.string,
        vol.Required(ATTR_CHANNEL): cv.url,
        vol.Optional(ATTR_EXPIRY): vol.Any(None, cv.positive_int),
        vol.Optional(ATTR_NAME): cv.string,
    })
)

WNS_ACCESS_TOKEN_URL = 'https://login.live.com/accesstoken.srf'


def get_service(hass, config, discovery_info=None):
    """Get the WNS service."""

    registrations_path = hass.config.path(REGISTRATIONS_FILE)
    registrations = load_json(registrations_path)

    client_id = config.get(CONF_CLIENT_ID)
    client_secret = config.get(CONF_CLIENT_SECRET)

    hass.http.register_view(WindowsPushRegistrationView(registrations,
                                                        registrations_path))

    return WindowsNotificationService(client_id, client_secret, registrations)


class WindowsNotificationService(BaseNotificationService):
    """Implement the notification service for WNS."""

    def __init__(self, client_id, client_secret, registrations):
        """Initialize the service."""
        self.client_id = client_id
        self.client_secret = client_secret
        self.registrations = registrations
        self.access_token = {
            'token': None,
            'expiry': datetime.now()
        }

    def send_message(self, message="", **kwargs):
        """Send SMS to specified target user cell."""

        headers = {
            "Authorization": f'Bearer {self.get_token()}',
            "X-WNS-Type": "wns/toast",
            "Content-Type": "text/xml"
        }

        title = kwargs.get(ATTR_TITLE, ATTR_TITLE_DEFAULT)
        content = f"<text>{title}</text>" \
                  f"<text>{message}</text>"

        data = kwargs.get(ATTR_DATA, {})

        launch = ""
        app_logo = "https://raw.githubusercontent.com/home-assistant/home-ass"\
                   "istant-iOS/master/icons/release_1024.png"

        if data:
            launch_data = data.get(ATTR_DATA_LAUNCH)
            if launch_data:
                launch = f" launch=\"{launch_data}\""

            hero_image = data.get(ATTR_DATA_HERO)
            if hero_image:
                content += f"<image src=\"{hero_image}\" placement=\"hero\" />"

            app_logo = data.get(ATTR_DATA_LOGO, app_logo)

        body = f"""<?xml version="1.0" encoding="utf-8"?>
        <toast{launch}>
            <visual>
                <binding template="ToastGeneric">
                    {content}
                    <image src="{app_logo}" \
                    placement="appLogoOverride" hint-crop="circle" />
                </binding>
            </visual>
        </toast>"""

        print(body)

        targets = kwargs.get(ATTR_TARGET)
        if not targets:
            targets = self.registrations.keys()

        for target in list(targets):
            target_channel = self.registrations.get(target)
            if target_channel is None:
                _LOGGER.error("%s is not a valid Windows push notification"
                              " target", target)
                continue

            channel = target_channel.get(ATTR_CHANNEL)
            requests.post(channel, body, headers=headers)

    def get_token(self):
        """Get a WNS access token to send push notification requests. If the
        token will expire in the next 5 minutes, a new one will be requested"""
        if self.access_token['expiry'] < datetime.now() + timedelta(minutes=5):
            body_data = {
                "grant_type": 'client_credentials',
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scope": 'notify.windows.com'
            }
            resp = requests.post(WNS_ACCESS_TOKEN_URL, body_data)
            resp_json = resp.json()
            expires_in = timedelta(seconds=resp_json['expires_in'])

            self.access_token['token'] = resp_json['access_token']
            self.access_token['expiry'] = datetime.now() + expires_in

        return self.access_token['token']


class WindowsPushRegistrationView(HomeAssistantView):
    """Accepts push registrations from a windows device."""

    url = '/api/notify.windows'
    name = 'api:notify.windows'

    def __init__(self, registrations, json_path):
        """Init WindowsPushRegistrationView."""
        self.registrations = registrations
        self.json_path = json_path

    async def post(self, request):
        """Accept requests for push registrations from a windows device."""
        try:
            data = await request.json()
        except ValueError:
            return self.json_message('Invalid JSON', HTTP_BAD_REQUEST)

        try:
            data = SUBSCRIPTION_SCHEMA(data)
        except vol.Invalid as ex:
            return self.json_message(
                humanize_error(data, ex), HTTP_BAD_REQUEST)

        channel_id = data.get(ATTR_CHANNEL_ID)
        previous_registration = self.registrations.get(channel_id)

        self.registrations[channel_id] = data

        try:
            hass = request.app['hass']

            await hass.async_add_job(save_json, self.json_path,
                                     self.registrations)
            return self.json_message(
                'Push notification subscriber registered.')
        except HomeAssistantError:
            if previous_registration is not None:
                self.registrations[channel_id] = previous_registration
            else:
                self.registrations.pop(channel_id)

            return self.json_message(
                'Error saving registration.', HTTP_INTERNAL_SERVER_ERROR)

    async def delete(self, request):
        """Delete a registration."""
        try:
            data = await request.json()
        except ValueError:
            return self.json_message('Invalid JSON', HTTP_BAD_REQUEST)

        channel_id = data.get(ATTR_CHANNEL_ID)

        found = None

        for key, registration in self.registrations.items():
            if registration.get(ATTR_CHANNEL_ID) == channel_id:
                found = key
                break

        if not found:
            # If not found, unregistering was already done. Return 200
            return self.json_message('Registration not found.')

        reg = self.registrations.pop(found)

        try:
            await request.app['hass'].async_add_job(save_json, self.json_path,
                                                    self.registrations)
        except HomeAssistantError:
            self.registrations[found] = reg
            return self.json_message(
                'Error saving registration.', HTTP_INTERNAL_SERVER_ERROR)

        return self.json_message('Push notification subscriber unregistered.')
