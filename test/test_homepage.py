import re

from datetime import datetime
from http import HTTPStatus
from unittest.mock import patch

import pytest

from fastapi.testclient import TestClient

from aurweb import db
from aurweb.asgi import app
from aurweb.models.account_type import USER_ID
from aurweb.models.package import Package
from aurweb.models.package_base import PackageBase
from aurweb.models.package_comaintainer import PackageComaintainer
from aurweb.models.package_request import PackageRequest
from aurweb.models.request_type import DELETION_ID, RequestType
from aurweb.models.user import User
from aurweb.redis import redis_connection
from aurweb.testing import setup_test_db
from aurweb.testing.html import parse_root
from aurweb.testing.requests import Request

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup():
    yield setup_test_db(
        User.__tablename__,
        Package.__tablename__,
        PackageBase.__tablename__,
        PackageComaintainer.__tablename__,
        PackageRequest.__tablename__
    )


@pytest.fixture
def user():
    with db.begin():
        user = db.create(User, Username="test", Email="test@example.org",
                         Passwd="testPassword", AccountTypeID=USER_ID)
    yield user


@pytest.fixture
def redis():
    redis = redis_connection()

    def delete_keys():
        # Cleanup keys if they exist.
        for key in ("package_count", "orphan_count", "user_count",
                    "trusted_user_count", "seven_days_old_added",
                    "seven_days_old_updated", "year_old_updated",
                    "never_updated", "package_updates"):
            if redis.get(key) is not None:
                redis.delete(key)

    delete_keys()
    yield redis
    delete_keys()


@pytest.fixture
def packages(user):
    """ Yield a list of num_packages Package objects maintained by user. """
    num_packages = 50  # Tunable

    # For i..num_packages, create a package named pkg_{i}.
    pkgs = []
    now = int(datetime.utcnow().timestamp())
    with db.begin():
        for i in range(num_packages):
            pkgbase = db.create(PackageBase, Name=f"pkg_{i}",
                                Maintainer=user, Packager=user,
                                SubmittedTS=now, ModifiedTS=now)
            pkg = db.create(Package, PackageBase=pkgbase, Name=pkgbase.Name)
            pkgs.append(pkg)
            now += 1

    yield pkgs


def test_homepage():
    with client as request:
        response = request.get("/")
    assert response.status_code == int(HTTPStatus.OK)


@patch('aurweb.util.get_ssh_fingerprints')
def test_homepage_ssh_fingerprints(get_ssh_fingerprints_mock):
    fingerprints = {'Ed25519': "SHA256:RFzBCUItH9LZS0cKB5UE6ceAYhBD5C8GeOBip8Z11+4"}
    get_ssh_fingerprints_mock.return_value = fingerprints

    with client as request:
        response = request.get("/")

    for key, value in fingerprints.items():
        assert key in response.content.decode()
        assert value in response.content.decode()
    assert 'The following SSH fingerprints are used for the AUR' in response.content.decode()


@patch('aurweb.util.get_ssh_fingerprints')
def test_homepage_no_ssh_fingerprints(get_ssh_fingerprints_mock):
    get_ssh_fingerprints_mock.return_value = {}

    with client as request:
        response = request.get("/")

    assert 'The following SSH fingerprints are used for the AUR' not in response.content.decode()


def test_homepage_stats(redis, packages):
    with client as request:
        response = request.get("/")
    assert response.status_code == int(HTTPStatus.OK)

    root = parse_root(response.text)

    expectations = [
        ("Packages", r'\d+'),
        ("Orphan Packages", r'\d+'),
        ("Packages added in the past 7 days", r'\d+'),
        ("Packages updated in the past 7 days", r'\d+'),
        ("Packages updated in the past year", r'\d+'),
        ("Packages never updated", r'\d+'),
        ("Registered Users", r'\d+'),
        ("Trusted Users", r'\d+')
    ]

    stats = root.xpath('//div[@id="pkg-stats"]//tr')
    for i, expected in enumerate(expectations):
        expected_key, expected_regex = expected
        key, value = stats[i].xpath('./td')
        assert key.text.strip() == expected_key
        assert re.match(expected_regex, value.text.strip())


def test_homepage_updates(redis, packages):
    with client as request:
        response = request.get("/")
        assert response.status_code == int(HTTPStatus.OK)
        # Run the request a second time to exercise the Redis path.
        response = request.get("/")
    assert response.status_code == int(HTTPStatus.OK)

    root = parse_root(response.text)

    # We expect to see the latest 15 packages, which happens to be
    # pkg_49 .. pkg_34. So, create a list of expectations using a range
    # starting at 49, stepping down to 49 - 15, -1 step at a time.
    expectations = [f"pkg_{i}" for i in range(50 - 1, 50 - 1 - 15, -1)]
    updates = root.xpath('//div[@id="pkg-updates"]/table/tbody/tr')
    for i, expected in enumerate(expectations):
        pkgname = updates[i].xpath('./td/a').pop(0)
        assert pkgname.text.strip() == expected


def test_homepage_dashboard(redis, packages, user):
    # Create Comaintainer records for all of the packages.
    with db.begin():
        for pkg in packages:
            db.create(PackageComaintainer,
                      PackageBase=pkg.PackageBase,
                      User=user, Priority=1)

    cookies = {"AURSID": user.login(Request(), "testPassword")}
    with client as request:
        response = request.get("/", cookies=cookies)
    assert response.status_code == int(HTTPStatus.OK)

    root = parse_root(response.text)

    # Assert some expectations that we end up getting all fifty
    # packages in the "My Packages" table.
    expectations = [f"pkg_{i}" for i in range(50 - 1, 0, -1)]
    my_packages = root.xpath('//table[@id="my-packages"]/tbody/tr')
    for i, expected in enumerate(expectations):
        name, version, votes, pop, voted, notify, desc, maint \
            = my_packages[i].xpath('./td')
        assert name.xpath('./a').pop(0).text.strip() == expected

    # Do the same for the Comaintained Packages table.
    my_packages = root.xpath('//table[@id="comaintained-packages"]/tbody/tr')
    for i, expected in enumerate(expectations):
        name, version, votes, pop, voted, notify, desc, maint \
            = my_packages[i].xpath('./td')
        assert name.xpath('./a').pop(0).text.strip() == expected


def test_homepage_dashboard_requests(redis, packages, user):
    now = int(datetime.utcnow().timestamp())

    pkg = packages[0]
    reqtype = db.query(RequestType, RequestType.ID == DELETION_ID).first()
    with db.begin():
        pkgreq = db.create(PackageRequest, PackageBase=pkg.PackageBase,
                           PackageBaseName=pkg.PackageBase.Name,
                           User=user, Comments=str(),
                           ClosureComment=str(), RequestTS=now,
                           RequestType=reqtype)

    cookies = {"AURSID": user.login(Request(), "testPassword")}
    with client as request:
        response = request.get("/", cookies=cookies)
    assert response.status_code == int(HTTPStatus.OK)

    root = parse_root(response.text)
    request = root.xpath('//table[@id="pkgreq-results"]/tbody/tr').pop(0)
    pkgname = request.xpath('./td/a').pop(0)
    assert pkgname.text.strip() == pkgreq.PackageBaseName


def test_homepage_dashboard_flagged_packages(redis, packages, user):
    # Set the first Package flagged by setting its OutOfDateTS column.
    pkg = packages[0]
    with db.begin():
        pkg.PackageBase.OutOfDateTS = int(datetime.utcnow().timestamp())

    cookies = {"AURSID": user.login(Request(), "testPassword")}
    with client as request:
        response = request.get("/", cookies=cookies)
    assert response.status_code == int(HTTPStatus.OK)

    # Check to see that the package showed up in the Flagged Packages table.
    root = parse_root(response.text)
    flagged_pkg = root.xpath('//table[@id="flagged-packages"]/tbody/tr').pop(0)
    flagged_name = flagged_pkg.xpath('./td/a').pop(0)
    assert flagged_name.text.strip() == pkg.Name
