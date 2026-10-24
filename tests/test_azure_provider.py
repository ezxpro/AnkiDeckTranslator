

def test_missing_region_warns_early():
    import contextlib
    err = io.StringIO()

    def offline(req, timeout=None):
        raise urllib.error.URLError("offline")

    with patch("ankitool.providers.azure.setting", lambda name, default="": ""), \
            patch("ankitool.providers.azure.urllib.request.urlopen", offline), \
            contextlib.redirect_stderr(err):
        AzureProvider(key="test")
    assert "AZURE_REGION is not set" in err.getvalue()