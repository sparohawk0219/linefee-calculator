"""블룸버그 Desktop API(localhost:8194)에서 현재값(BDP)과 시계열(BDH)을 가져오는 최소 래퍼."""
from contextlib import contextmanager

import blpapi


@contextmanager
def _refdata():
    opts = blpapi.SessionOptions()
    opts.setServerHost("localhost")
    opts.setServerPort(8194)
    session = blpapi.Session(opts)
    if not session.start() or not session.openService("//blp/refdata"):
        raise ConnectionError("블룸버그 연결 실패 (터미널 로그인 확인)")
    try:
        yield session, session.getService("//blp/refdata")
    finally:
        session.stop()


def _responses(session):
    while True:
        ev = session.nextEvent(10000)
        for msg in ev:
            if ev.eventType() in (blpapi.Event.RESPONSE, blpapi.Event.PARTIAL_RESPONSE):
                yield msg
        if ev.eventType() in (blpapi.Event.RESPONSE, blpapi.Event.TIMEOUT):
            return


def bdp(tickers: list[str], fields: list[str]) -> dict:
    """{ticker: {field: value}}. 값이 없거나 티커가 틀리면 해당 항목이 빠진다."""
    out = {}
    with _refdata() as (session, svc):
        req = svc.createRequest("ReferenceDataRequest")
        for t in tickers:
            req.append("securities", t)
        for f in fields:
            req.append("fields", f)
        session.sendRequest(req)
        for msg in _responses(session):
            for sec in msg.getElement("securityData").values():
                name = sec.getElementAsString("security")
                row = {}
                if sec.hasElement("securityError"):
                    row["_error"] = sec.getElement("securityError").getElementAsString("message")
                fd = sec.getElement("fieldData")
                for f in fields:
                    if fd.hasElement(f):
                        row[f] = fd.getElement(f).getValue()
                out[name] = row
    return out


def bdh(ticker: str, field: str, start: str, end: str) -> list[tuple]:
    """[(date, value), ...]. start/end는 'YYYYMMDD'."""
    rows = []
    with _refdata() as (session, svc):
        req = svc.createRequest("HistoricalDataRequest")
        req.append("securities", ticker)
        req.append("fields", field)
        req.set("startDate", start)
        req.set("endDate", end)
        session.sendRequest(req)
        for msg in _responses(session):
            data = msg.getElement("securityData").getElement("fieldData")
            for point in data.values():
                if point.hasElement(field):
                    rows.append((point.getElementAsDatetime("date"),
                                 point.getElementAsFloat(field)))
    return rows
