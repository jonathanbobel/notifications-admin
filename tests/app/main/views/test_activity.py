import json
import uuid
from functools import partial
from urllib.parse import parse_qs, urlparse

import pytest
from flask import url_for
from freezegun import freeze_time

from app.main.views.jobs import get_status_filters, get_time_left
from app.models.service import Service
from tests.conftest import (
    SERVICE_ONE_ID,
    create_active_caseworking_user,
    create_active_user_view_permissions,
    create_active_user_with_permissions,
    create_notifications,
    normalize_spaces,
)


@pytest.mark.parametrize(
    "user,extra_args,expected_update_endpoint,expected_limit_days,page_title",
    [
        (
            create_active_user_view_permissions(),
            {"message_type": "email"},
            "/email.json",
            7,
            "Emails",
        ),
        (
            create_active_user_view_permissions(),
            {"message_type": "sms"},
            "/sms.json",
            7,
            "Text messages",
        ),
        (
            create_active_caseworking_user(),
            {},
            ".json",
            None,
            "Sent messages",
        ),
    ],
)
@pytest.mark.parametrize(
    "status_argument, expected_api_call",
    [
        (
            "",
            [
                "created",
                "pending",
                "sending",
                "pending-virus-check",
                "delivered",
                "sent",
                "returned-letter",
                "failed",
                "temporary-failure",
                "permanent-failure",
                "technical-failure",
                "virus-scan-failed",
                "validation-failed",
            ],
        ),
        ("sending", ["sending", "created", "pending", "pending-virus-check"]),
        ("delivered", ["delivered", "sent", "returned-letter"]),
        (
            "failed",
            [
                "failed",
                "temporary-failure",
                "permanent-failure",
                "technical-failure",
                "virus-scan-failed",
                "validation-failed",
            ],
        ),
    ],
)
@pytest.mark.parametrize("page_argument, expected_page_argument", [(1, 1), (22, 22), (None, 1)])
@pytest.mark.parametrize(
    "to_argument, expected_to_argument",
    [
        ("", ""),
        ("+447900900123", "+447900900123"),
        ("test@example.com", "test@example.com"),
    ],
)
@freeze_time("2020-01-01 01:00")
def test_can_show_notifications(
    client_request,
    service_one,
    mock_get_notifications,
    mock_get_service_statistics,
    mock_get_service_data_retention,
    mock_has_no_jobs,
    mock_get_no_api_keys,
    user,
    extra_args,
    expected_update_endpoint,
    expected_limit_days,
    page_title,
    status_argument,
    expected_api_call,
    page_argument,
    expected_page_argument,
    to_argument,
    expected_to_argument,
    mocker,
):
    client_request.login(user)
    if expected_to_argument:
        page = client_request.post(
            "main.view_notifications",
            service_id=SERVICE_ONE_ID,
            status=status_argument,
            page=page_argument,
            _data={"to": to_argument},
            _expected_status=200,
            **extra_args
        )
    else:
        page = client_request.get(
            "main.view_notifications",
            service_id=SERVICE_ONE_ID,
            status=status_argument,
            page=page_argument,
            **extra_args
        )
    first_row = page.select_one("tbody tr")
    assert normalize_spaces(first_row.select_one("a.file-list-filename.govuk-link").text) == (
        # Comes from
        # https://github.com/alphagov/notifications-admin/blob/8faffad508f9a087b0006989c197741c693cc2e2/tests/__init__.py#L436
        "07123456789"
    )
    assert normalize_spaces(
        # We’re doing str() here not .text to make sure there’s no extra
        # HTML sneaking in
        str(first_row.select_one(".file-list-hint"))
    ) == (
        # Comes from
        # https://github.com/alphagov/notifications-admin/blob/8faffad508f9a087b0006989c197741c693cc2e2/tests/__init__.py#L271
        "template content"
    ) or (
        # Comes from
        # https://github.com/alphagov/notifications-admin/blob/8faffad508f9a087b0006989c197741c693cc2e2/tests/__init__.py#L273
        "template subject"
    )

    assert normalize_spaces(first_row.select_one(".table-field-right-aligned .align-with-message-body").text) == (
        "Delivered 1 January at 1:01am"
    )

    assert page_title in page.select_one("h1").text.strip()

    path_to_json = page.select_one("div[data-key=notifications]")["data-resource"]

    url = urlparse(path_to_json)
    assert url.path == "/services/{}/notifications{}".format(
        SERVICE_ONE_ID,
        expected_update_endpoint,
    )
    query_dict = parse_qs(url.query)
    if status_argument:
        assert query_dict["status"] == [status_argument]
    if expected_page_argument:
        assert query_dict["page"] == [str(expected_page_argument)]
    assert "to" not in query_dict

    mock_get_notifications.assert_called_with(
        limit_days=expected_limit_days,
        page=expected_page_argument,
        service_id=SERVICE_ONE_ID,
        status=expected_api_call,
        template_type=list(extra_args.values()),
        to=expected_to_argument,
    )

    json_response = client_request.get_response(
        "json_updates.get_notifications_page_partials_as_json",
        service_id=service_one["id"],
        status=status_argument,
        **extra_args
    )
    json_content = json.loads(json_response.get_data(as_text=True))
    assert json_content.keys() == {"counts", "notifications", "service_data_retention_days"}

    # All links to view individual notifications should pass through the statuses for the current view,
    # so that backlinks can be generated correctly.
    view_notification_links = page.select(".file-list-filename")
    assert all(
        parse_qs(urlparse(view_notification_link["href"]).query, keep_blank_values=True)["from_statuses"]
        == [status_argument]
        for view_notification_link in view_notification_links
    )


def test_can_show_notifications_if_data_retention_not_available(
    client_request,
    mock_get_notifications,
    mock_get_service_statistics,
    mock_has_no_jobs,
    mock_get_no_api_keys,
):
    page = client_request.get(
        "main.view_notifications",
        service_id=SERVICE_ONE_ID,
        status="sending,delivered,failed",
    )
    assert page.select_one("h1").text.strip() == "Messages"


@pytest.mark.parametrize(
    "user, query_parameters, expected_download_link",
    [
        (
            create_active_user_with_permissions(),
            {},
            partial(
                url_for,
                ".download_notifications_csv",
                message_type=None,
            ),
        ),
        (
            create_active_user_with_permissions(),
            {"status": "failed"},
            partial(url_for, ".download_notifications_csv", status="failed"),
        ),
        (
            create_active_user_with_permissions(),
            {"message_type": "sms"},
            partial(
                url_for,
                ".download_notifications_csv",
                message_type="sms",
            ),
        ),
        (
            create_active_user_view_permissions(),
            {},
            partial(
                url_for,
                ".download_notifications_csv",
            ),
        ),
        (
            create_active_caseworking_user(),
            {},
            lambda service_id: None,
        ),
    ],
)
def test_link_to_download_notifications(
    client_request,
    mock_get_notifications,
    mock_get_service_statistics,
    mock_get_service_data_retention,
    mock_has_no_jobs,
    mock_get_no_api_keys,
    user,
    query_parameters,
    expected_download_link,
):
    client_request.login(user)
    page = client_request.get("main.view_notifications", service_id=SERVICE_ONE_ID, **query_parameters)
    download_link = page.select_one("a[download=download]")
    assert (download_link["href"] if download_link else None) == expected_download_link(service_id=SERVICE_ONE_ID)


def test_download_not_available_to_users_without_dashboard(
    client_request,
    active_caseworking_user,
):
    client_request.login(active_caseworking_user)
    client_request.get(
        "main.download_notifications_csv",
        service_id=SERVICE_ONE_ID,
        _expected_status=403,
    )


def test_letters_with_status_virus_scan_failed_shows_a_failure_description(
    mocker,
    client_request,
    service_one,
    mock_get_service_statistics,
    mock_get_service_data_retention,
    mock_get_api_keys,
):
    notifications = create_notifications(
        template_type="letter",
        status="virus-scan-failed",
        is_precompiled_letter=True,
        client_reference="client reference",
    )
    mocker.patch("app.notification_api_client.get_notifications_for_service", return_value=notifications)

    page = client_request.get(
        "main.view_notifications",
        service_id=service_one["id"],
        message_type="letter",
        status="",
    )

    error_description = page.select_one("div.table-field-status-error").text.strip()
    assert "Virus detected\n" in error_description


@pytest.mark.parametrize("letter_status", ["pending-virus-check", "virus-scan-failed"])
def test_should_not_show_preview_link_for_precompiled_letters_in_virus_states(
    mocker,
    client_request,
    service_one,
    mock_get_service_statistics,
    mock_get_service_data_retention,
    mock_get_no_api_keys,
    letter_status,
):
    notifications = create_notifications(
        template_type="letter", status=letter_status, is_precompiled_letter=True, client_reference="ref"
    )
    mocker.patch("app.notification_api_client.get_notifications_for_service", return_value=notifications)

    page = client_request.get(
        "main.view_notifications",
        service_id=service_one["id"],
        message_type="letter",
        status="",
    )

    assert not page.select_one("a.file-list-filename")


def test_shows_message_when_no_notifications(
    client_request,
    mock_get_service_statistics,
    mock_get_service_data_retention,
    mock_get_notifications_with_no_notifications,
    mock_get_no_api_keys,
):

    page = client_request.get(
        "main.view_notifications",
        service_id=SERVICE_ONE_ID,
        message_type="sms",
    )

    assert normalize_spaces(page.select("tbody tr")[0].text) == "No messages found (messages are kept for 7 days)"


@pytest.mark.parametrize(
    "initial_query_arguments,form_post_data,expected_search_box_label,expected_search_box_contents",
    [
        (
            {},
            {},
            "Search by recipient",
            None,
        ),
        (
            {
                "message_type": "sms",
            },
            {},
            "Search by phone number",
            None,
        ),
        (
            {
                "message_type": "sms",
            },
            {
                "to": "+33(0)5-12-34-56-78",
            },
            "Search by phone number",
            "+33(0)5-12-34-56-78",
        ),
        (
            {
                "status": "failed",
                "message_type": "email",
                "page": "99",
            },
            {
                "to": "test@example.com",
            },
            "Search by email address",
            "test@example.com",
        ),
        (
            {
                "message_type": "letter",
            },
            {
                "to": "Firstname Lastname",
            },
            "Search by postal address or file name",
            "Firstname Lastname",
        ),
    ],
)
def test_search_recipient_form(
    client_request,
    mock_get_notifications,
    mock_get_service_statistics,
    mock_get_service_data_retention,
    mock_get_no_api_keys,
    initial_query_arguments,
    form_post_data,
    expected_search_box_label,
    expected_search_box_contents,
):
    page = client_request.post(
        "main.view_notifications",
        service_id=SERVICE_ONE_ID,
        _data=form_post_data,
        _expected_status=200,
        **initial_query_arguments
    )

    assert page.select_one("form")["method"] == "post"
    action_url = page.select_one("form")["action"]
    url = urlparse(action_url)
    assert url.path == "/services/{}/notifications/{}".format(
        SERVICE_ONE_ID, initial_query_arguments.get("message_type", "")
    ).rstrip("/")
    query_dict = parse_qs(url.query)
    assert query_dict == {}

    assert page.select_one("label[for=to]").text.strip() == expected_search_box_label

    recipient_inputs = page.select("input[name=to]")
    assert len(recipient_inputs) == 2

    for field in recipient_inputs:
        assert field.get("value") == expected_search_box_contents


@pytest.mark.parametrize(
    "message_type, expected_search_box_label",
    [
        (None, "Search by recipient or reference"),
        ("sms", "Search by phone number or reference"),
        ("email", "Search by email address or reference"),
        ("letter", "Search by postal address, file name or reference"),
    ],
)
def test_api_users_are_told_they_can_search_by_reference_when_service_has_api_keys(
    client_request,
    mocker,
    fake_uuid,
    mock_get_notifications,
    mock_get_service_statistics,
    mock_get_service_data_retention,
    message_type,
    expected_search_box_label,
    mock_get_api_keys,
):
    page = client_request.get(
        "main.view_notifications",
        service_id=SERVICE_ONE_ID,
        message_type=message_type,
    )
    assert page.select_one("label[for=to]").text.strip() == expected_search_box_label


@pytest.mark.parametrize(
    "message_type, expected_search_box_label",
    [
        (None, "Search by recipient"),
        ("sms", "Search by phone number"),
        ("email", "Search by email address"),
        ("letter", "Search by postal address or file name"),
    ],
)
def test_api_users_are_not_told_they_can_search_by_reference_when_service_has_no_api_keys(
    client_request,
    mocker,
    fake_uuid,
    mock_get_notifications,
    mock_get_service_statistics,
    mock_get_service_data_retention,
    message_type,
    expected_search_box_label,
    mock_get_no_api_keys,
):
    page = client_request.get(
        "main.view_notifications",
        service_id=SERVICE_ONE_ID,
        message_type=message_type,
    )
    assert page.select_one("label[for=to]").text.strip() == expected_search_box_label


def test_should_show_notifications_for_a_service_with_next_previous(
    client_request,
    service_one,
    active_user_with_permissions,
    mock_get_notifications_with_previous_next,
    mock_get_service_statistics,
    mock_get_service_data_retention,
    mock_get_no_api_keys,
    mocker,
):
    page = client_request.get("main.view_notifications", service_id=service_one["id"], message_type="sms", page=2)

    next_page_link = page.select_one("a[rel=next]")
    prev_page_link = page.select_one("a[rel=previous]")
    assert (
        url_for("main.view_notifications", service_id=service_one["id"], message_type="sms", page=3)
        in next_page_link["href"]
    )
    assert "Next page" in next_page_link.text.strip()
    assert "page 3" in next_page_link.text.strip()
    assert (
        url_for("main.view_notifications", service_id=service_one["id"], message_type="sms", page=1)
        in prev_page_link["href"]
    )
    assert "Previous page" in prev_page_link.text.strip()
    assert "page 1" in prev_page_link.text.strip()


def test_doesnt_show_pagination_with_search_term(
    client_request,
    service_one,
    active_user_with_permissions,
    mock_get_notifications_with_previous_next,
    mock_get_service_statistics,
    mock_get_service_data_retention,
    mock_get_no_api_keys,
    mocker,
):
    page = client_request.post(
        "main.view_notifications",
        service_id=service_one["id"],
        message_type="sms",
        _data={
            "to": "test@example.com",
        },
        _expected_status=200,
    )
    assert len(page.select("tbody tr")) == 50
    assert not page.select_one("a[rel=next]")
    assert not page.select_one("a[rel=previous]")
    assert normalize_spaces(page.select_one(".table-show-more-link").text) == "Only showing the first 50 messages"


@pytest.mark.parametrize(
    "job_created_at, expected_message",
    [
        ("2016-01-10 11:09:00.000000+00:00", "Data available for 7 days"),
        ("2016-01-04 11:09:00.000000+00:00", "Data available for 1 day"),
        ("2016-01-03 11:09:00.000000+00:00", "Data available for 12 hours"),
        ("2016-01-02 23:59:59.000000+00:00", "Data no longer available"),
    ],
)
@freeze_time("2016-01-10 12:00:00.000000")
def test_time_left(job_created_at, expected_message):
    assert get_time_left(job_created_at) == expected_message


STATISTICS = {"sms": {"requested": 6, "failed": 2, "delivered": 1}}


def test_get_status_filters_calculates_stats(client_request):
    ret = get_status_filters(Service({"id": "foo"}), "sms", STATISTICS)

    assert {label: count for label, _option, _link, count in ret} == {
        "total": 6,
        "sending": 3,
        "failed": 2,
        "delivered": 1,
    }


def test_get_status_filters_in_right_order(client_request):
    ret = get_status_filters(Service({"id": "foo"}), "sms", STATISTICS)

    assert [label for label, _option, _link, _count in ret] == ["total", "sending", "delivered", "failed"]


def test_get_status_filters_constructs_links(client_request):
    ret = get_status_filters(Service({"id": "foo"}), "sms", STATISTICS)

    link = ret[0][2]
    assert link == "/services/foo/notifications/sms?status={}".format("sending,delivered,failed")


def test_html_contains_notification_id(
    client_request,
    service_one,
    active_user_with_permissions,
    mock_get_notifications,
    mock_get_service_statistics,
    mock_get_service_data_retention,
    mock_get_no_api_keys,
    mocker,
):
    page = client_request.get(
        "main.view_notifications",
        service_id=service_one["id"],
        message_type="sms",
        status="",
    )

    notifications = page.select("tbody tr")
    for tr in notifications:
        assert uuid.UUID(tr.attrs["id"])


def test_html_contains_links_for_failed_notifications(
    client_request,
    mock_get_service_statistics,
    mock_get_service_data_retention,
    mock_get_no_api_keys,
    mocker,
):
    notifications = create_notifications(status="technical-failure")
    mocker.patch("app.notification_api_client.get_notifications_for_service", return_value=notifications)

    response = client_request.get(
        "main.view_notifications", service_id=SERVICE_ONE_ID, message_type="sms", status="sending%2Cdelivered%2Cfailed"
    )
    notifications = response.select("tbody tr")
    for tr in notifications:
        link_text = tr.select_one("div.table-field-status-error a").text
        assert normalize_spaces(link_text) == "Technical failure"


@pytest.mark.parametrize(
    "notification_type, expected_row_contents",
    (
        ("sms", "07123456789 hello & welcome hidden"),
        ("email", "example@gov.uk hidden, hello & welcome"),
        (
            "letter",
            (
                # Letters don’t support redaction, but this test is still
                # worthwhile to show that the ampersand is not double-escaped
                "1 Example Street ((name)), hello & welcome"
            ),
        ),
    ),
)
def test_redacts_templates_that_should_be_redacted(
    client_request,
    mocker,
    mock_get_service_statistics,
    mock_get_service_data_retention,
    mock_get_no_api_keys,
    notification_type,
    expected_row_contents,
):
    notifications = create_notifications(
        status="technical-failure",
        content="hello & welcome ((name))",
        subject="((name)), hello & welcome",
        personalisation={"name": "Jo"},
        redact_personalisation=True,
        template_type=notification_type,
    )
    mocker.patch("app.notification_api_client.get_notifications_for_service", return_value=notifications)

    page = client_request.get(
        "main.view_notifications",
        service_id=SERVICE_ONE_ID,
        message_type=notification_type,
    )

    assert normalize_spaces(page.select("tbody tr th")[0].text) == (expected_row_contents)


@pytest.mark.parametrize("message_type, nav_visible", [("email", True), ("sms", True), ("letter", False)])
def test_big_numbers_dont_show_for_letters(
    client_request,
    service_one,
    mock_get_notifications,
    active_user_with_permissions,
    mock_get_service_statistics,
    mock_get_service_data_retention,
    mock_get_no_api_keys,
    message_type,
    nav_visible,
):
    page = client_request.get(
        "main.view_notifications",
        service_id=service_one["id"],
        message_type=message_type,
        status="",
        page=1,
    )

    assert (len(page.select(".pill")) > 0) == nav_visible
    assert (len(page.select("[type=search]")) > 0) is True


@freeze_time("2017-09-27 16:30:00.000000")
@pytest.mark.parametrize(
    "message_type, status, expected_hint_status, single_line",
    [
        ("email", "created", "Sending since 27 September at 5:30pm", True),
        ("email", "sending", "Sending since 27 September at 5:30pm", True),
        ("email", "temporary-failure", "Inbox not accepting messages right now 27 September at 5:31pm", False),
        ("email", "permanent-failure", "Email address does not exist 27 September at 5:31pm", False),
        ("email", "delivered", "Delivered 27 September at 5:31pm", True),
        ("sms", "created", "Sending since 27 September at 5:30pm", True),
        ("sms", "sending", "Sending since 27 September at 5:30pm", True),
        ("sms", "temporary-failure", "Phone not accepting messages right now 27 September at 5:31pm", False),
        ("sms", "permanent-failure", "Not delivered 27 September at 5:31pm", False),
        ("sms", "delivered", "Delivered 27 September at 5:31pm", True),
        ("letter", "created", "27 September at 5:30pm", True),
        ("letter", "pending-virus-check", "27 September at 5:30pm", True),
        ("letter", "sending", "27 September at 5:30pm", True),
        ("letter", "delivered", "27 September at 5:30pm", True),
        ("letter", "received", "27 September at 5:30pm", True),
        ("letter", "accepted", "27 September at 5:30pm", True),
        ("letter", "cancelled", "27 September at 5:30pm", False),  # The API won’t return cancelled letters
        ("letter", "permanent-failure", "Permanent failure 27 September at 5:31pm", False),
        ("letter", "temporary-failure", "27 September at 5:30pm", False),  # Not currently a real letter status
        ("letter", "virus-scan-failed", "Virus detected 27 September at 5:30pm", False),
        ("letter", "validation-failed", "Validation failed 27 September at 5:30pm", False),
        ("letter", "technical-failure", "Technical failure 27 September at 5:30pm", False),
    ],
)
def test_sending_status_hint_displays_correctly_on_notifications_page(
    client_request,
    service_one,
    mock_get_service_statistics,
    mock_get_service_data_retention,
    mock_get_no_api_keys,
    message_type,
    status,
    expected_hint_status,
    single_line,
    mocker,
):
    notifications = create_notifications(template_type=message_type, status=status)
    mocker.patch("app.notification_api_client.get_notifications_for_service", return_value=notifications)

    page = client_request.get("main.view_notifications", service_id=service_one["id"], message_type=message_type)

    assert normalize_spaces(page.select(".table-field-right-aligned")[0].text) == expected_hint_status
    assert bool(page.select(".align-with-message-body")) is single_line


@pytest.mark.parametrize(
    "is_precompiled_letter,expected_address,expected_hint",
    [
        (True, "Full Name\nFirst address line\npostcode", "ref"),
        (False, "Full Name\nFirst address line\npostcode", "template subject"),
    ],
)
def test_should_show_address_and_hint_for_letters(
    client_request,
    service_one,
    mock_get_service_statistics,
    mock_get_service_data_retention,
    mock_get_no_api_keys,
    mocker,
    is_precompiled_letter,
    expected_address,
    expected_hint,
):
    notifications = create_notifications(
        template_type="letter",
        subject=expected_hint,
        is_precompiled_letter=is_precompiled_letter,
        client_reference=expected_hint,
        to=expected_address,
    )
    mocker.patch("app.notification_api_client.get_notifications_for_service", return_value=notifications)

    page = client_request.get(
        "main.view_notifications",
        service_id=SERVICE_ONE_ID,
        message_type="letter",
    )

    assert page.select_one("a.file-list-filename").text == "Full Name, First address line, postcode"
    assert page.select_one("p.file-list-hint").text.strip() == expected_hint
