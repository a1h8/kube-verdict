from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass
class FirefoxSession:
    session_id: str
    base_url: str = "http://127.0.0.1:4444"

    def _url(self, suffix: str) -> str:
        return f"{self.base_url}/session/{self.session_id}{suffix}"

    def _request(self, method: str, suffix: str, payload: dict | None = None) -> dict:
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(
            self._url(suffix),
            data=data,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(req, timeout=60) as response:
            return json.load(response)

    def navigate(self, url: str) -> None:
        self._request("POST", "/url", {"url": url})

    def title(self) -> str:
        return self._request("GET", "/title").get("value", "")

    def source(self) -> str:
        return self._request("GET", "/source").get("value", "")

    def execute(self, script: str, args: list | None = None):
        return self._request("POST", "/execute/sync", {"script": script, "args": args or []}).get("value")

    def find_xpath(self, xpath: str):
        payload = {"using": "xpath", "value": xpath}
        return self._request("POST", "/element", payload).get("value")

    def find_all_xpath(self, xpath: str):
        payload = {"using": "xpath", "value": xpath}
        return self._request("POST", "/elements", payload).get("value", [])

    def click(self, element_id: str) -> None:
        self._request("POST", f"/element/{element_id}/click", {})

    def clear(self, element_id: str) -> None:
        self._request("POST", f"/element/{element_id}/clear", {})

    def send_keys(self, element_id: str, text: str) -> None:
        self._request("POST", f"/element/{element_id}/value", {"text": text, "value": list(text)})

    def screenshot(self) -> str:
        return self._request("GET", "/screenshot").get("value", "")

    def set_window_rect(self, x: int, y: int, width: int, height: int) -> None:
        self._request("POST", "/window/rect", {"x": x, "y": y, "width": width, "height": height})

    def delete(self) -> None:
        self._request("DELETE", "", {})


def create_session(base_url: str = "http://127.0.0.1:4444") -> FirefoxSession:
    payload = {
        "capabilities": {
            "alwaysMatch": {
                "browserName": "firefox",
                "acceptInsecureCerts": True,
                "moz:firefoxOptions": {
                    "binary": "/Applications/Firefox.app/Contents/MacOS/firefox",
                },
            }
        }
    }
    req = urllib.request.Request(
        f"{base_url}/session",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        body = json.load(response)
    return FirefoxSession(session_id=body["value"]["sessionId"], base_url=base_url)


def wait_for_xpath(session: FirefoxSession, xpath: str, timeout: float = 30.0, poll: float = 0.5) -> str:
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            element = session.find_xpath(xpath)
            if element:
                return element["element-6066-11e4-a52e-4f735466cecf"]
        except urllib.error.HTTPError as exc:
            last_error = exc
        time.sleep(poll)
    raise RuntimeError(f"Timed out waiting for xpath: {xpath}") from last_error
