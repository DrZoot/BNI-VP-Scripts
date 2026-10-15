"""Authenticated CSR downloads. Never persist credentials or authentication tokens."""

import base64
import contextlib
import io
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup
import requests

from bni_report import BASE_URL, BNISession, REPORT_PALMS_URL
from parsers.csr import ReportError, normalise


CONNECT_API = "https://api.bniconnectglobal.com"
REPORT_API = f"{CONNECT_API}/reports-api/reports/v1"
R2Y_API = "https://apibni.reporting2you.com/graphql"


class TimeoutSession(requests.Session):
    def request(self, method, url, **kwargs):
        kwargs.setdefault("timeout", 30)
        return super().request(method, url, **kwargs)


def _request(session, method, url, **kwargs):
    try:
        response = session.request(method, url, **kwargs)
    except requests.RequestException as error:
        # requests exception messages can contain redirect URLs with tokens.
        raise ReportError(f"Network request failed ({type(error).__name__})") from None
    if response.status_code != 200:
        raise ReportError(f"Reporting service returned HTTP {response.status_code}")
    return response


def _json(response):
    try:
        result = response.json()
    except ValueError:
        raise ReportError("Reporting service returned an invalid JSON response") from None
    if not isinstance(result, dict):
        raise ReportError("Reporting service returned an invalid JSON object")
    return result


def _html(response, title):
    text = response.text
    if title.casefold() not in BeautifulSoup(text, "html.parser").get_text(" ", strip=True).casefold():
        raise ReportError(f"Expected {title}; received an error page or another report")
    return text


class ConnectReports:
    def __init__(self, username, password, chapter):
        if not username or not password:
            raise ReportError("BNI Connect username/password are missing from .env")
        self.chapter = chapter
        self.client = BNISession(username, password)
        headers = dict(self.client.session.headers)
        self.client.session.close()
        self.client.session = TimeoutSession()
        self.client.session.headers.update(headers)
        self.reports = None
        self.country_id = None
        try:
            # The shared legacy login writes its username and server responses
            # to stdout. Do not allow them into the CSR summary or diagnostics.
            with contextlib.redirect_stdout(io.StringIO()):
                success = self.client.login()
        except (requests.RequestException, ValueError):
            raise ReportError("BNI Connect sign-in failed") from None
        if not success:
            raise ReportError("BNI Connect sign-in failed")

    def close(self):
        self.client.session.close()
        if self.reports is not None:
            self.reports.close()

    def _legacy(self, url, parameters, title):
        response = _request(self.client.session, "GET", url,
                            params={**parameters, "__exports_details": "1"})
        soup = BeautifulSoup(response.text, "html.parser")
        iframe = soup.find("iframe", src=True)
        if iframe:
            iframe_url = urljoin(BASE_URL, iframe["src"])
            if urlparse(iframe_url).netloc != urlparse(BASE_URL).netloc:
                raise ReportError("Unexpected BNI report host")
            response = _request(self.client.session, "GET", iframe_url, headers={"Referer": url})
        return _html(response, title)

    def palms(self, start, end):
        return self._legacy(REPORT_PALMS_URL, {
            "IdOrg": self.chapter["id"], "startDate": start.strftime("%m/%d/%Y"),
            "endDate": end.strftime("%m/%d/%Y"), "ShowDetails": "false",
        }, "Summary PALMS Report")

    def dues(self, as_of):
        return self._legacy(f"{BASE_URL}/web/secure/reportsChapterMembershipDues", {
            "IdOrg": self.chapter["id"], "endDate": as_of.strftime("%m/%d/%Y"),
        }, "Membership Dues Report")

    def _open_reports(self):
        if self.reports is not None:
            return
        response = _request(self.client.session, "GET", f"{BASE_URL}/web/secure/reportsredirect",
                            params={"tab": "chapter"})
        master = parse_qs(urlparse(response.url).query).get("mtoken", [None])[0]
        if not master:
            raise ReportError("BNI report sign-in did not supply a master token")
        session = TimeoutSession()
        try:
            response = _request(session, "POST", f"{CONNECT_API}/auth-api/authenticate-token",
                                json={"masterToken": master, "variant": "connect"}, headers={
                                    "Content-Type": "application/json",
                                    "Authorization": "Basic " + base64.b64encode(b"IDENTITY_PORTAL:").decode(),
                                })
            access = (_json(response).get("content") or {}).get("access_token")
            if not access:
                raise ReportError("BNI report token exchange failed")
            session.headers.update({"Authorization": f"Bearer {access}", "Content-Type": "application/json"})
            response = _request(session, "POST", f"{REPORT_API}/org/filter", json={
                "url": "/web/secure/reportsHome#reportsactiontabcontentstructureregional",
                "org_sub_classes": "1,2,3", "list_flag": "Y",
            })
            countries = (_json(response).get("content") or {}).get("orgs_filter_menu", [])
            for country in countries:
                for region in country.get("sub", []):
                    for chapter in region.get("sub", []):
                        if str(chapter.get("id")) == self.chapter["id"] and str(region.get("id")) == self.chapter["region_id"]:
                            self.country_id = country["id"]
            if self.country_id is None:
                raise ReportError("Configured chapter is not available in BNI report permissions")
            self.reports = session
        except Exception:
            session.close()
            raise

    def traffic_lights(self, month):
        self._open_reports()
        menu = "menu.reports.chapter.trafficlights"
        response = _request(self.reports, "GET", f"{REPORT_API}/execution-criteria", params={"menuName": menu})
        criteria = _json(response).get("content") or {}
        if criteria.get("execution_type") != "SHORT":
            raise ReportError("Chapter Traffic Lights requires an asynchronous report; direct download is unavailable")
        response = _request(self.reports, "GET", f"{REPORT_API}/generate", params={
            "menuName": menu, "IdRegion": self.chapter["region_id"], "IdOrg": self.chapter["id"],
            "Month": month.strftime("%m"), "Year": str(month.year), "ReportType": "HTML",
            "__heading_chapter": self.chapter["id"], "__heading_region": self.chapter["region_id"],
            "__heading_country": self.country_id, "__orientation": "ltr", "execution_type": "SHORT",
        }, timeout=60)
        return _html(response, "Traffic Lights Report")


class Reporting2You:
    def __init__(self, username, password, chapter):
        if not username or not password:
            raise ReportError("r2y_username/r2y_password are missing from .env")
        self.chapter = chapter
        self.session = TimeoutSession()
        try:
            data = self.query("mutation($email:String!,$password:String!){login(email:$email,password:$password){token}}",
                              {"email": username, "password": password})
            token = (data.get("login") or {}).get("token")
            if not token:
                raise ReportError("Reporting2You sign-in failed")
            self.session.headers["Authorization"] = f"Bearer {token}"
            data = self.query("query($email:String!){loginVariables(email:$email){memberGroup memberIdRegion memberRole memberDashBoardLT memberDashBoardMC}}",
                              {"email": username})
            context = data.get("loginVariables") or {}
            if (normalise(context.get("memberGroup", "")) != normalise(chapter["name"])
                    or str(context.get("memberIdRegion")) != chapter["region_id"]):
                raise ReportError("Reporting2You account chapter does not match the configured chapter")
            role = "LT" if context.get("memberDashBoardLT") else "MC" if context.get("memberDashBoardMC") else None
            if not role:
                raise ReportError("Reporting2You account does not have chapter reporting access")
            self.context = {"type": role, "typeId": context["memberGroup"], "regionId": chapter["region_id"], "group": context["memberGroup"]}
        except Exception:
            self.session.close()
            raise

    def close(self):
        self.session.close()

    def query(self, query, variables):
        response = _request(self.session, "POST", R2Y_API, json={"query": query, "variables": variables})
        result = _json(response)
        # Do not echo server errors: they may contain credentials or query inputs.
        if result.get("errors") or not isinstance(result.get("data"), dict):
            raise ReportError("Reporting2You query failed or access was denied")
        return result["data"]

    def members(self):
        data = self.query("query($group:String,$regionId:String,$type:String!,$typeId:String!){memberTrafficLights(input:{group:$group,regionId:$regionId,type:$type,typeId:$typeId}){id grupo idRegiao membro cor pontos joinDate renewalDate}}", self.context)
        rows = data.get("memberTrafficLights")
        if not isinstance(rows, list):
            raise ReportError("Reporting2You member report is missing")
        return {"members": rows}

    def histories(self, members):
        """The aggregate evolution API fails for this account; use its working history API."""
        histories = {}
        rows = members.get("members", [])
        for start in range(0, len(rows), 8):
            batch = rows[start:start + 8]
            declarations = ",".join(f"$id{i}:Long!" for i in range(len(batch)))
            fields = " ".join(f"m{i}:rxMemberById(memberTrafficLightId:$id{i}){{anoMes pontos cor grupo idRegiao}}" for i in range(len(batch)))
            try:
                variables = {f"id{i}": int(row["id"]) for i, row in enumerate(batch)}
            except (KeyError, ValueError, TypeError):
                raise ReportError("Reporting2You member IDs are missing or invalid") from None
            data = self.query(f"query({declarations}){{{fields}}}", variables)
            for i, row in enumerate(batch):
                history = data.get(f"m{i}")
                if not isinstance(history, list):
                    raise ReportError("Reporting2You member history is incomplete")
                histories[str(row["id"])] = history
        return {"histories": histories}
