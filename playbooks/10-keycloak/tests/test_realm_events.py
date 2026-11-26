"""Static check for the Keycloak event log lines that the Loki alerts read.

realm_events.tftest.hcl checks the events resource with a mocked provider.
This check makes sure that the Keycloak CR keeps the default jboss-logging
levels and the default console log (error events at WARN, plain text lines).
"""

from pathlib import Path
import re
import unittest


LAYER = Path(__file__).resolve().parents[1]
MAIN_CHART = LAYER / "charts" / "h3xinfra-keycloak-main"
# The listener options, the log handlers (log), the root log level and the
# console format or output can remove or change the lines of the Loki rules.
FORBIDDEN = re.compile(
    r"spi-events-listener|SPI_EVENTS_LISTENER|\blog-(level|console)|\bKC_LOG(_|\b)"
    r"|name:\s*[\"']?log[\"']?\s*$", re.M)


class RealmEventsTests(unittest.TestCase):
    def test_keycloak_keeps_the_default_event_log_lines(self):
        for path in [MAIN_CHART / "values.yaml", *sorted((MAIN_CHART / "templates").glob("*"))]:
            with self.subTest(path=path.name):
                self.assertNotRegex(path.read_text(), FORBIDDEN)


if __name__ == "__main__":
    unittest.main()
