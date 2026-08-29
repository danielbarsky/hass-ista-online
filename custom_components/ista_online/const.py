"""Constants for the ista Online integration."""

DOMAIN = "ista_online"
PLATFORMS = ["sensor"]

CONF_COUNTRY = "country"
CONF_BASE_URL = "base_url"

# The only API host known to serve the Danish portal's OAuth2/meter endpoints.
# Despite the "beta" name it is the live backend; the production hostnames under
# istaonline.dk are wildcard DNS to the ASP.NET web portal and expose no API.
COUNTRY_OPTIONS = {
    "Denmark": "https://service.istaonlinebeta.dk",
}

DEFAULT_COUNTRY = "Denmark"

# Portal data is monthly. Polling more often than this only re-sends
# credentials and burns API calls for a value that changes ~12 times a year.
UPDATE_INTERVAL_SECONDS = 6 * 3600

# Renew the bearer token this long before it actually expires.
TOKEN_EXPIRY_MARGIN_SECONDS = 300

REQUEST_TIMEOUT_SECONDS = 30
