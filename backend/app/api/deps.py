"""Dependency access to the service container. (Shared contract)"""
from __future__ import annotations

from fastapi import Request

from .ports import Services


def get_services(request: Request) -> Services:
    return request.app.state.services
