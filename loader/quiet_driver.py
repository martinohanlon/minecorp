"""One way for the loaders to open a Neo4j driver, without the server's "unrecognized" notifications.

Neo4j warns when a query names a label, relationship type or property the database has not seen yet ("The relationship type COMPLETED does not
exist"). The loaders ask such questions on purpose (does any task have COMPLETED history? are there MENTIONS yet?) on a database that may not have
them, so the warning is noise: the query still runs and answers correctly. Only that one category is switched off; other notifications (a
deprecated syntax, a cartesian product) still show.
"""
from __future__ import annotations

import neo4j
from neo4j import GraphDatabase

# the driver renamed the setting from "categories" to "classifications"; the loaders accept any driver from 5.20
_SETTINGS = (("notifications_disabled_classifications", "NotificationDisabledClassification"),
             ("notifications_disabled_categories", "NotificationDisabledCategory"))


def open_driver(uri: str, user: str, password: str):
    """A driver for the loaders: the same as GraphDatabase.driver, with "unrecognized" notifications switched off where the driver can."""
    for setting, enum_name in _SETTINGS:
        kind = getattr(neo4j, enum_name, None)
        if kind is None:
            continue
        try:
            return GraphDatabase.driver(uri, auth=(user, password), **{setting: [kind.UNRECOGNIZED]})
        except (TypeError, ValueError, neo4j.exceptions.ConfigurationError):
            continue
    return GraphDatabase.driver(uri, auth=(user, password))
