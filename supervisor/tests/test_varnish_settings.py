import pytest
from pydantic import ValidationError
from app.services.varnish import VarnishSettings


@pytest.mark.parametrize("value", [{"size_mb": 2}, {"size_mb": True}, {"storage": "file,/tmp/x"},
                                  {"log_retention_days": 0}, {"path": "/tmp/cache"}])
def test_invalid_storage_settings(value):
    with pytest.raises(ValidationError):
        VarnishSettings.model_validate(value)


def test_valid_storage_settings():
    assert VarnishSettings(storage="malloc", size_mb=512, log_retention_days=30).model_dump() == {
        "storage": "malloc", "size_mb": 512, "log_retention_days": 30}
