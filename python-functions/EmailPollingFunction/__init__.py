import logging

import azure.functions as func

from shared.email_ingestion import poll_active_watch_mailboxes


def main(mytimer: func.TimerRequest) -> None:
    if mytimer.past_due:
        logging.warning("EmailPollingFunction timer is past due")

    logging.info("EmailPollingFunction triggered")
    result = poll_active_watch_mailboxes()
    logging.info(
        (
            "EmailPollingFunction completed: mailboxes=%s inserted=%s skipped=%s "
            "route_matched=%s route_no_match=%s forward_ready=%s "
            "forward_validation_failed=%s errors=%s"
        ),
        result["mailboxes"],
        result["inserted"],
        result["skipped"],
        result["route_matched"],
        result["route_no_match"],
        result["forward_ready"],
        result["forward_validation_failed"],
        result["errors"],
    )
