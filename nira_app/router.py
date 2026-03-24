import re
from dataclasses import dataclass
from typing import Callable


@dataclass
class Response:
    status: str
    body: str | bytes
    content_type: str = "text/html; charset=utf-8"
    headers: list[tuple[str, str]] | None = None

    def to_wsgi(self, start_response):
        response_headers = [("Content-Type", self.content_type)]
        if self.headers:
            response_headers.extend(self.headers)
        payload = self.body.encode("utf-8") if isinstance(self.body, str) else self.body
        response_headers.append(("Content-Length", str(len(payload))))
        start_response(self.status, response_headers)
        return [payload]


class Router:
    def __init__(self):
        self.routes: list[tuple[str, re.Pattern, Callable]] = []

    def add(self, method: str, path_pattern: str, handler: Callable):
        regex_pattern = re.sub(r"\{([^}]+)\}", r"(?P<\1>[^/]+)", path_pattern)
        regex_pattern = f"^{regex_pattern}$"
        self.routes.append((method.upper(), re.compile(regex_pattern), handler))

    def match(self, method: str, path: str) -> tuple[Callable, dict[str, str]] | None:
        for route_method, pattern, handler in self.routes:
            if route_method == method:
                match = pattern.match(path)
                if match:
                    return handler, match.groupdict()
        return None
