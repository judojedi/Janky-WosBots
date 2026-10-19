"""Client for the official Whiteout Survival gift-code redemption API.

Matches the official site's own requests:
  POST https://wos-giftcode-api.centurygame.com/api/gift_code
  form-encoded fields: fid, cdk, kid, time (unix seconds), sign

Signing: sort keys alphabetically, join as k=v&k=v, append the site's
client secret with no separator, MD5-hex -> `sign` field.

State (kid) resolution: there is no public player-lookup endpoint. Instead,
probe with a dummy code that can never be real ("ZZ" + 14 random chars):
  - err_code 40020 (USER INFO ERROR) -> fid+kid did NOT resolve
  - any other real verdict from the server -> fid+kid resolved
Probes never redeem anything.
"""
import hashlib
import random
import string
import time

API_URL = "https://wos-giftcode-api.centurygame.com/api/gift_code"
SIGN_SECRET = "tB87#kPtkxqOS2"  # shipped in the site's own public JS bundle

ORIGIN = "https://wos-giftcode.centurygame.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Origin": ORIGIN,
    "Referer": ORIGIN + "/",
    "Content-Type": "application/x-www-form-urlencoded",
}

# Status strings returned by redeem_code()
SUCCESS = "success"
ALREADY_CLAIMED = "already_claimed"
USAGE_LIMIT = "usage_limit"
CDK_NOT_FOUND = "code_not_found"
TIME_ERROR = "time_error"
TIMEOUT_RETRY = "retry_later"      # transient: HTTP 429/5xx, 40004, or 40019
STATE_MISMATCH = "state_mismatch"  # 40020: kid (state) is wrong for this fid
ROLE_NOT_EXIST = "player_not_found"
SIGN_ERROR = "sign_error"
LEVEL_TOO_LOW = "level_too_low"    # furnace/VIP-gated code
LOGIN_EXPIRED = "login_expired"
UNKNOWN = "unknown"


class RedeemError(Exception):
    """Transport-level failure (network, HTTP error, malformed response)."""


def sign_payload(params: dict) -> dict:
    body = {k: str(v) for k, v in params.items()}
    query = "&".join(f"{k}={body[k]}" for k in sorted(body))
    body["sign"] = hashlib.md5((query + SIGN_SECRET).encode()).hexdigest()
    return body


async def _post_form(session, payload: dict) -> tuple[int, dict]:
    """POST form-encoded payload. Returns (http_status, json_body)."""
    try:
        async with session.post(
            API_URL, data=sign_payload(payload), headers=HEADERS, timeout=30
        ) as resp:
            status = resp.status
            try:
                body = await resp.json()
            except Exception:
                body = {}
    except Exception as e:
        raise RedeemError(f"gift_code request failed: {e}")
    if not isinstance(body, dict):
        body = {}
    return status, body


def classify_response(status: int, body: dict) -> str:
    """Map the API's (msg, err_code) verdict to a status string."""
    if status in (429, 502, 503, 504):
        return TIMEOUT_RETRY
    msg = str(body.get("msg", "")).strip(".")
    err = body.get("err_code")

    if msg == "SUCCESS":
        return SUCCESS
    if msg == "RECEIVED" and err == 40008:
        return ALREADY_CLAIMED
    if msg == "SAME TYPE EXCHANGE" and err == 40011:
        return ALREADY_CLAIMED
    if msg == "TIME ERROR" and err == 40007:
        return TIME_ERROR
    if msg == "CDK NOT FOUND" and err == 40014:
        return CDK_NOT_FOUND
    if msg == "USED" and err == 40005:
        return USAGE_LIMIT
    if msg == "TIMEOUT RETRY" and err == 40004:
        return TIMEOUT_RETRY
    if msg == "TOO FREQUENT" and err == 40019:
        return TIMEOUT_RETRY
    if msg == "NOT LOGIN":
        return LOGIN_EXPIRED
    if err == 40001 and "not exist" in msg.lower():
        return ROLE_NOT_EXIST
    if msg == "USER INFO ERROR" and err == 40020:
        return STATE_MISMATCH
    if "sign error" in msg.lower():
        return SIGN_ERROR
    if (msg == "STOVE_LV ERROR" and err == 40006) or (
        msg in ("RECHARGE_MONEY ERROR", "RECHARGE_MONEY_VIP ERROR")
        and err in (40017, 40018)
    ):
        return LEVEL_TOO_LOW
    return UNKNOWN


async def redeem_code(session, fid: str, kid: str, code: str) -> str:
    """Redeem `code` for player `fid` in state `kid`.

    Returns a status string (SUCCESS, ALREADY_CLAIMED, ...).
    Raises RedeemError only for transport-level failures (caller may retry).
    """
    status, body = await _post_form(
        session,
        {"fid": str(fid), "cdk": code, "kid": str(kid), "time": str(int(time.time()))},
    )
    return classify_response(status, body)


def _dummy_code() -> str:
    return "ZZ" + "".join(random.choices(string.ascii_uppercase + string.digits, k=14))


async def probe_kid(session, fid: str, kid: str) -> str:
    """Check whether state `kid` resolves for player `fid` without redeeming.

    Returns "match" | "nomatch" | "throttle" | "error".
    """
    try:
        status, body = await _post_form(
            session,
            {
                "fid": str(fid),
                "cdk": _dummy_code(),
                "kid": str(kid),
                "time": str(int(time.time())),
            },
        )
    except RedeemError:
        return "error"
    if status in (429, 502, 503, 504):
        return "throttle"
    err = body.get("err_code")
    if err == 40019:  # TOO FREQUENT
        return "throttle"
    if err == 40020:  # USER INFO ERROR - fid+kid didn't resolve
        return "nomatch"
    if body.get("msg"):  # any real verdict -> fid+kid resolved
        return "match"
    return "error"
