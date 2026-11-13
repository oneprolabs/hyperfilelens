"""Business-resource scope of one organization Role assignment, not ownership."""

OWN = "own"
ORGANIZATION = "organization"
RESOURCE_SCOPE_CHOICES = (
    (OWN, "Owned Resources"),
    (ORGANIZATION, "All Organization Resources"),
)
