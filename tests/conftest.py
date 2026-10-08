import logging

import pytest

from adl.domains.retail import runtime

logging.getLogger("mcp").setLevel(logging.CRITICAL)


@pytest.fixture(scope="session")
def lake():
    return runtime.lake()


@pytest.fixture(scope="session")
def valued():
    return runtime.valued()


@pytest.fixture()
def gw():
    """A gateway with an empty audit log over the shared lake."""
    return runtime.fresh_gateway()
