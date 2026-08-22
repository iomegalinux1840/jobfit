from jobfit.privacy import redact_for_cloud


def test_redacts_english_contact_and_identity_data_but_preserves_career_content():
    result = redact_for_cloud(
        """John Smith | AI Engineer
john.smith@example.com | +1 (514) 555-1234
123 Main Street, Montreal, QC H1A 1A1
SIN: 123 456 789
Passport No: X12345678

Experience
Python and manufacturing automation. John Smith led an ERP project.
"""
    )

    assert "John Smith" not in result.text
    assert "john.smith@example.com" not in result.text
    assert "514" not in result.text
    assert "123 Main Street" not in result.text
    assert "123 456 789" not in result.text
    assert "X12345678" not in result.text
    assert "Python and manufacturing automation" in result.text
    assert result.counts == {
        "name": 1,
        "email": 1,
        "phone": 1,
        "social_id": 1,
        "identity_id": 1,
        "address": 1,
    }


def test_redacts_french_contact_and_identity_labels():
    result = redact_for_cloud(
        """Marie-Claire Dupont
marie.dupont@example.fr | 01 23 45 67 89
12 rue de la Paix, 75001 Paris
Numéro de sécurité sociale : 2 85 12 75 123 456 78
Permis de conduire n° : AB123456

Expérience
Python et automatisation industrielle.
"""
    )

    assert "Marie-Claire Dupont" not in result.text
    assert "marie.dupont@example.fr" not in result.text
    assert "01 23 45 67 89" not in result.text
    assert "12 rue de la Paix" not in result.text
    assert "2 85 12 75 123 456 78" not in result.text
    assert "AB123456" not in result.text
    assert "Python et automatisation industrielle" in result.text
    assert result.counts == {
        "name": 1,
        "email": 1,
        "phone": 1,
        "social_id": 1,
        "identity_id": 1,
        "address": 1,
    }


def test_does_not_guess_at_work_history_names_or_locations():
    result = redact_for_cloud(
        """Alex Candidate

Experience
Worked with Marie Dupont in Montreal on a Python platform.
"""
    )

    assert "[REDACTED_NAME]" in result.text
    assert "Marie Dupont" in result.text
    assert "Montreal" in result.text


def test_detects_name_when_header_also_contains_contact_details():
    result = redact_for_cloud(
        "John Smith | john.smith@example.com | +1 514-555-1234\nAI Engineer\n"
    )

    assert "John Smith" not in result.text
    assert "john.smith@example.com" not in result.text
    assert "514-555-1234" not in result.text
    assert "AI Engineer" in result.text
