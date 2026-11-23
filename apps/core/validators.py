"""Field validators for Stage 3a core models.

Used by PhotographerProfile in models.py:
  - country      -> validate_country_code
  - currency     -> validate_currency_code
  - timezone     -> validate_timezone_name
  - state_code   -> validate_gst_state_code

Each validator raises django.core.exceptions.ValidationError for invalid input.
Validators never modify the value they check.
"""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError

# ISO 3166-1 alpha-2 country codes.
ISO_3166_1_ALPHA2 = frozenset(
    """
    AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ
    BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ
    CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ
    DE DJ DK DM DO DZ
    EC EE EG EH ER ES ET
    FI FJ FK FM FO FR
    GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY
    HK HM HN HR HT HU
    ID IE IL IM IN IO IQ IR IS IT
    JE JM JO JP
    KE KG KH KI KM KN KP KR KW KY KZ
    LA LB LC LI LK LR LS LT LU LV LY
    MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ
    NA NC NE NF NG NI NL NO NP NR NU NZ
    OM
    PA PE PF PG PH PK PL PM PN PR PS PT PW PY
    QA
    RE RO RS RU RW
    SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ
    TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ
    UA UG UM US UY UZ
    VA VC VE VG VI VN VU
    WF WS
    YE YT
    ZA ZM ZW
    """.split()
)

# Supported invoice currencies (ISO 4217). INR is required for
# GST-registered studios (enforced in models.py).
SUPPORTED_CURRENCY_CODES = frozenset(
    {"INR", "USD", "EUR", "GBP", "AUD", "CAD", "SGD", "AED", "JPY", "CHF", "NZD", "HKD"}
)

# Indian GST state and union territory codes: 01 through 38 inclusive, plus
# 97 (Other Territory) and 99 (Centre Jurisdiction), as approved.
# Note: 25 falls inside the 01-38 range and is included here as instructed,
# although it is no longer an active GST state code.
GST_STATE_CODES = frozenset(
    [f"{n:02d}" for n in range(1, 39)] + ["97", "99"]
)


def validate_country_code(value):
    """Accept only an uppercase ISO 3166-1 alpha-2 code, such as 'IN'."""
    if not isinstance(value, str) or value not in ISO_3166_1_ALPHA2:
        raise ValidationError(
            "Enter a valid ISO 3166-1 alpha-2 country code (for example, IN).",
            code="invalid_country_code",
        )


def validate_currency_code(value):
    """Accept only a supported uppercase ISO 4217 currency code, such as 'INR'."""
    if not isinstance(value, str) or value not in SUPPORTED_CURRENCY_CODES:
        raise ValidationError(
            "Enter a supported currency code (for example, INR).",
            code="invalid_currency_code",
        )


def validate_timezone_name(value):
    """Accept only a timezone name that zoneinfo can load, such as 'Asia/Kolkata'.

    On Windows, zoneinfo needs the IANA database. Install the 'tzdata' package
    if loading names fails there.
    """
    if not isinstance(value, str) or not value:
        raise ValidationError(
            "Enter a valid IANA timezone name (for example, Asia/Kolkata).",
            code="invalid_timezone",
        )
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError, OSError):
        raise ValidationError(
            "Enter a valid IANA timezone name (for example, Asia/Kolkata).",
            code="invalid_timezone",
        )


def validate_gst_state_code(value):
    """Accept an empty value or a supported two-digit GST state code, such as '27'.

    An empty value is allowed here. models.py decides when a state code is
    required, which is when the studio is GST-registered.
    """
    if value in (None, ""):
        return
    if not isinstance(value, str) or value not in GST_STATE_CODES:
        raise ValidationError(
            "Enter a valid two-digit GST state code (for example, 27).",
            code="invalid_gst_state_code",
        )
