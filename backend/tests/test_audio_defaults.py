"""Per-model audio defaults: a default voice / format / speed for speech and a default language / format for transcription, applied only when the
client left that setting out, and only on the audio routes."""
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete

from app import db, proxy
from app.main import app
from app.models import Endpoint, Model, RequestLog
from app.router import health


class Up:
    def __init__(self):
        self.calls: list = []   # (path, content-type, body bytes)

    def handler(self, request: httpx.Request):
        p = request.url.path
        self.calls.append((p, request.headers.get("content-type", ""), request.content))
        if p.endswith("/audio/speech"):
            return httpx.Response(200, content=b"AUDIO", headers={"content-type": "audio/mpeg"})
        if "/audio/" in p:
            return httpx.Response(200, json={"text": "hello"})
        return httpx.Response(200, json={"id": "c", "object": "chat.completion", "choices": [{"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})


@pytest.fixture(scope="module")
def client():
    db.init_db()
    yield TestClient(app)
    with Session(db.engine) as s:
        s.exec(delete(RequestLog))
        s.commit()


@pytest.fixture
def up(monkeypatch):
    u = Up()
    monkeypatch.setattr(proxy, "client", httpx.AsyncClient(transport=httpx.MockTransport(u.handler)))
    return u


def mk(caps):
    with Session(db.engine) as s:
        ep = Endpoint(name=f"ad-{time.time_ns()}", base_url="http://up/v1", status="online"); s.add(ep); s.commit(); s.refresh(ep)
        m = Model(endpoint_id=ep.id, upstream_id="u", capabilities=caps, enabled=True, max_slots=2); s.add(m); s.commit(); s.refresh(m)
        health.status[ep.id] = "online"
        return m.id


def gateway(client, **model_fields):
    mid = mk(model_fields.pop("caps"))
    r = client.post("/api/gateways", json={"name": f"ad-{time.time_ns()}", "models": [{"public_name": "m", "members": [{"model_id": mid}], **model_fields}]})
    return r


def ok_gateway(client, **kw):
    r = gateway(client, **kw)
    assert r.status_code == 200, r.text
    return r.json()


def url(g, route):
    return f"/api/admin-gw/{g['slug']}/v1/{route}"


SPEECH = {"speech": {"voice": "af", "response_format": "wav", "speed": 1.25}}


def test_speech_defaults_fill_what_the_client_left_out(client, up):
    g = ok_gateway(client, caps=["tts"], voices=["af", "am"], audio_defaults=SPEECH)
    assert g["models"][0]["audio_defaults"] == SPEECH
    assert client.post(url(g, "audio/speech"), json={"model": "m", "input": "hi"}).status_code == 200
    assert json.loads(up.calls[-1][2]) == {"model": "u", "input": "hi", "voice": "af", "response_format": "wav", "speed": 1.25}
    client.post(url(g, "audio/speech"), json={"model": "m", "input": "hi", "voice": "am", "speed": 2.0})          # the client's own values win
    sent = json.loads(up.calls[-1][2])
    assert sent["voice"] == "am" and sent["speed"] == 2.0 and sent["response_format"] == "wav"


def test_transcription_defaults_apply_to_multipart_requests_and_translations_get_no_language(client, up):
    g = ok_gateway(client, caps=["audio"], audio_defaults={"transcription": {"language": "pl", "response_format": "verbose_json", "temperature": 0.2}})
    files = {"file": ("a.wav", b"RIFFxxxx", "audio/wav")}
    client.post(url(g, "audio/transcriptions"), data={"model": "m"}, files=files)
    body = up.calls[-1][2].decode("latin-1")
    assert 'name="language"\r\n\r\npl' in body and 'name="response_format"\r\n\r\nverbose_json' in body and 'name="temperature"\r\n\r\n0.2' in body
    client.post(url(g, "audio/transcriptions"), data={"model": "m", "language": "en"}, files=files)
    body = up.calls[-1][2].decode("latin-1")
    assert body.count('name="language"') == 1 and 'name="language"\r\n\r\nen' in body
    client.post(url(g, "audio/translations"), data={"model": "m"}, files=files)
    body = up.calls[-1][2].decode("latin-1")
    assert 'name="language"' not in body and 'name="response_format"\r\n\r\nverbose_json' in body


def test_chat_and_other_models_are_untouched(client, up):
    g = ok_gateway(client, caps=["chat"], audio_defaults=SPEECH, default_params={"temperature": 0.3})
    client.post(url(g, "chat/completions"), json={"model": "m", "messages": [{"role": "user", "content": "x"}]})
    sent = json.loads(up.calls[-1][2])
    assert "voice" not in sent and "speed" not in sent and sent["temperature"] == 0.3
    plain = ok_gateway(client, caps=["tts"])
    assert plain["models"][0]["audio_defaults"] is None
    client.post(url(plain, "audio/speech"), json={"model": "m", "input": "hi"})
    assert json.loads(up.calls[-1][2]) == {"model": "u", "input": "hi"}


@pytest.mark.parametrize("defaults,msg", [
    ({"speech": {"response_format": "ogg"}}, "Speech format"),
    ({"speech": {"speed": 9}}, "between 0.25 and 4"),
    ({"speech": {"speed": "fast"}}, "must be a number"),
    ({"speech": {"voice": "zz"}}, "not in this model's voice list"),
    ({"transcription": {"language": "p1"}}, "language code"),
    ({"transcription": {"response_format": "mp3"}}, "Transcription format"),
    ({"transcription": {"temperature": 2}}, "between 0 and 1"),
])
def test_invalid_defaults_are_refused_with_a_clear_message(client, defaults, msg):
    r = gateway(client, caps=["tts"], voices=["af"], audio_defaults=defaults)
    assert r.status_code == 400 and msg in r.json()["detail"] and "'m'" in r.json()["detail"]


def test_defaults_are_normalised_updated_and_cleared(client, up):
    g = ok_gateway(client, caps=["tts"], audio_defaults={"speech": {"voice": " af ", "speed": "1.5", "bogus": 1}, "other": {"x": 1}, "transcription": {}})
    assert g["models"][0]["audio_defaults"] == {"speech": {"voice": "af", "speed": 1.5}}          # trimmed, typed, unknown keys dropped
    body = {"name": g["name"], "slug": g["slug"], "models": [{**{k: v for k, v in g["models"][0].items() if k in ("public_name", "enabled")},
                                                              "members": [{"model_id": g["models"][0]["members"][0]["model_id"]}], "audio_defaults": {}}]}
    r = client.put(f"/api/gateways/{g['id']}", json=body)
    assert r.status_code == 200 and r.json()["models"][0]["audio_defaults"] is None
