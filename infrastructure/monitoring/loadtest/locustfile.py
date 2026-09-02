"""
Load Testing with Locust

Simulates:
- 100K SMS/day
- Thousands of concurrent bookings
- API endpoint stress testing

Usage:
    locust -f locustfile.py --host=http://localhost:8000
"""

from locust import HttpUser, task, between, events
import random
import string
import json


def random_phone():
    """Generate a random phone number."""
    return f"+1{random.randint(5550000000, 5559999999)}"


def random_email():
    """Generate a random email."""
    username = ''.join(random.choices(string.ascii_lowercase, k=8))
    return f"{username}@test.com"


def random_name():
    """Generate a random name."""
    first_names = ["John", "Jane", "Bob", "Alice", "Charlie", "Diana", "Eve", "Frank"]
    last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller"]
    return random.choice(first_names), random.choice(last_names)


class LeadIngestionUser(HttpUser):
    """Simulates lead ingestion via API."""
    wait_time = between(1, 3)

    @task(10)
    def create_lead(self):
        """Create a new lead."""
        first_name, last_name = random_name()
        payload = {
            "source": random.choice(["api", "webhook", "csv_import", "facebook", "google"]),
            "first_name": first_name,
            "last_name": last_name,
            "phone": random_phone(),
            "email": random_email(),
            "state": random.choice(["FL", "TX", "CA", "NY", "OH"]),
        }

        self.client.post(
            "/api/v1/ingestion/api",
            json=payload,
            headers={"Authorization": f"Bearer {self.token}"},
        )


class BookingUser(HttpUser):
    """Simulates booking flow."""
    wait_time = between(2, 5)

    @task(5)
    def get_available_slots(self):
        """Get available appointment slots."""
        self.client.get(
            "/api/v1/booking/slots",
            headers={"Authorization": f"Bearer {self.token}"},
        )

    @task(3)
    def start_booking(self):
        """Start a booking flow."""
        if hasattr(self, 'lead_id'):
            self.client.post(
                "/api/v1/booking/start",
                json={"lead_id": self.lead_id},
                headers={"Authorization": f"Bearer {self.token}"},
            )

    @task(2)
    def select_slot(self):
        """Select a booking slot."""
        if hasattr(self, 'lead_id') and hasattr(self, 'conversation_id'):
            self.client.post(
                "/api/v1/booking/select",
                json={
                    "lead_id": self.lead_id,
                    "conversation_id": self.conversation_id,
                    "reply": str(random.randint(1, 3)),
                },
                headers={"Authorization": f"Bearer {self.token}"},
            )


class APIReadUser(HttpUser):
    """Simulates API read operations."""
    wait_time = between(0.5, 2)

    @task(20)
    def list_leads(self):
        """List leads."""
        self.client.get(
            "/api/v1/leads",
            headers={"Authorization": f"Bearer {self.token}"},
        )

    @task(15)
    def list_appointments(self):
        """List appointments."""
        self.client.get(
            "/api/v1/appointments",
            headers={"Authorization": f"Bearer {self.token}"},
        )

    @task(10)
    def get_dashboard(self):
        """Get agent dashboard."""
        self.client.get(
            "/api/v1/agent/dashboard",
            headers={"Authorization": f"Bearer {self.token}"},
        )

    @task(5)
    def get_analytics(self):
        """Get analytics overview."""
        self.client.get(
            "/api/v1/admin/analytics/overview",
            headers={"Authorization": f"Bearer {self.token}"},
        )


class SMSUser(HttpUser):
    """Simulates SMS sending."""
    wait_time = between(0.1, 0.5)

    @task(50)
    def send_sms(self):
        """Send an SMS (simulated)."""
        # In load testing, we simulate the SMS endpoint
        # without actually calling Engage Clouds
        self.client.post(
            "/api/v1/realtime/notify",
            json={
                "type": "test",
                "title": "Load Test",
                "message": "This is a load test message",
            },
            headers={"Authorization": f"Bearer {self.token}"},
        )


# Event hooks
@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Called when the test starts."""
    print("Load test starting...")


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Called when the test stops."""
    print("Load test completed.")


# Custom user for health checks
class HealthCheckUser(HttpUser):
    """Simulates health check requests."""
    wait_time = between(1, 1)

    @task(1)
    def health_check(self):
        """Check API health."""
        self.client.get("/health")
