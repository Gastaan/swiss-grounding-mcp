"""Company lookup in the federal UID register (public web service, no key)."""

from __future__ import annotations

import re
from xml.sax.saxutils import escape

from lxml import etree

from .. import http
from ..models import Citation, ToolResult, needs, source_error, today

URL = "https://www.uid-wse.admin.ch/V5.0/PublicServices.svc"
NS_UID = "http://www.uid.admin.ch/xmlns/uid-wse"
# eCH-0108 commercialRegisterStatus
REGISTER_STATUS = {"1": "unknown", "2": "registered in the commercial register", "3": "deleted from the commercial register"}


def _envelope(body: str) -> str:
    return ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
            f"{body}</s:Body></s:Envelope>")


def _t(el, name: str) -> str | None:
    found = el.xpath(f'.//*[local-name()="{name}"]/text()')
    return found[0].strip() if found else None


async def company_lookup(name_or_uid: str | None, limit: int = 5) -> ToolResult:
    if not name_or_uid or not name_or_uid.strip():
        return needs("name_or_uid", "Which company (name or UID like CHE-123.456.789)?")
    q = name_or_uid.strip()
    is_uid = re.fullmatch(r"(?i)CHE[-.\s]?\d{3}[.\s]?\d{3}[.\s]?\d{3}(\s*(MWST|TVA|IVA))?", q)
    if is_uid:
        uid = re.sub(r"\D", "", q)
        body = (f'<uid:GetByUID xmlns:uid="{NS_UID}"><uid:uid><uidOrganisationIdCategorie '
                f'xmlns="http://www.ech.ch/xmlns/eCH-0097/5">CHE</uidOrganisationIdCategorie><uidOrganisationId '
                f'xmlns="http://www.ech.ch/xmlns/eCH-0097/5">{uid}</uidOrganisationId></uid:uid></uid:GetByUID>')
        action = "GetByUID"
    else:
        body = (f'<uid:Search xmlns:uid="{NS_UID}"><uid:searchParameters><u5:uidEntitySearchParameters '
                f'xmlns:u5="{NS_UID}/5"><u5:organisationName>{escape(q)}</u5:organisationName>'
                "</u5:uidEntitySearchParameters></uid:searchParameters></uid:Search>")
        action = "Search"
    try:
        xml = await http.fetch(URL, method="POST", data=_envelope(body), ttl=http.DAY, check_robots=False,
                               headers={"Content-Type": "text/xml; charset=utf-8",
                                        "SOAPAction": f'"{NS_UID}/IPublicServices/{action}"'})
    except http.FetchError as e:
        return source_error("The UID register", e)
    root = etree.fromstring(xml.encode())
    orgs = root.xpath('//*[local-name()="organisation"][*[local-name()="organisationIdentification"]]')
    results = []
    for org in orgs[:limit]:
        uid_num = _t(org, "uidOrganisationId")
        info = org.getparent()
        legal = org.xpath('.//*[local-name()="address"][*[local-name()="addressCategory"]="LEGAL"]')
        addr = legal[0] if legal else org
        results.append({
            "name": _t(org, "organisationName"),
            "uid": f"CHE-{uid_num[:3]}.{uid_num[3:6]}.{uid_num[6:]}" if uid_num else None,
            "uid_digits": uid_num,
            "legal_seat": _t(addr, "town"), "canton": _t(addr, "cantonAbbreviation"),
            "address": " ".join(x for x in (_t(addr, "street"), _t(addr, "houseNumber"),
                                            _t(addr, "swissZipCode"), _t(addr, "town")) if x),
            "commercial_register": REGISTER_STATUS.get(_t(info, "commercialRegisterStatus") or "", "not registered"),
            "vat_registered": _t(info, "vatStatus") == "2",
        })
    if not results:
        return ToolResult(status="not_found", summary=f"No entry for '{q}' in the UID register.",
                          guidance="The name must match the registered company name; try the exact legal name or UID.",
                          citations=[Citation(title="UID register search", url="https://www.uid.admin.ch/",
                                              publisher="Federal Statistical Office FSO", level="federal",
                                              jurisdiction="CH")])
    first = results[0]
    return ToolResult(
        status="ok",
        summary=f"{first['name']} ({first['uid']}), seat {first['legal_seat']} {first['canton']}: "
        f"{first['commercial_register']}" + (", VAT-registered." if first["vat_registered"] else "."),
        data={"companies": [{k: v for k, v in r.items() if k != "uid_digits"} for r in results],
              "matches": len(orgs)},
        citations=[Citation(title=f"UID register — {r['name']}",
                            url=f"https://www.uid.admin.ch/Detail.aspx?uid_id=CHE{r['uid_digits']}",
                            publisher="Federal Statistical Office FSO (UID register)", level="federal",
                            jurisdiction=f"CH-{r['canton']}" if r["canton"] else "CH", retrieved_at=today())
                   for r in results[:3]],
        guidance="Official register data. For the full commercial register extract, point to zefix.ch.",
    )
