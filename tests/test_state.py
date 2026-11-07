import json
import os

from tesla_ocpp_bridge.state import State


def test_missing_file_gives_empty_state(tmp_path):
    st = State(str(tmp_path / "nope.json"))
    assert st.register_wh == 0 and st.tx is None and st.outbox == [] and st.tx_ids == {}


def test_round_trip(tmp_path):
    path = str(tmp_path / "sub" / "state.json")  # parent dir is created on save
    st = State(path)
    st.register_wh = 1234.5
    st.tx = {"local_id": "abc", "meter_start": 1000}
    st.outbox = [{"kind": "start", "local_id": "abc"}]
    st.tx_ids = {"abc": 7}
    st.config = {"HeartbeatInterval": 60}
    st.save()

    again = State(path)
    assert again.register_wh == 1234.5
    assert again.tx == st.tx
    assert again.outbox == st.outbox
    assert again.tx_ids == {"abc": 7}
    assert again.config == {"HeartbeatInterval": 60}


def test_save_leaves_no_temp_file(tmp_path):
    path = tmp_path / "state.json"
    State(str(path)).save()
    assert path.exists()
    assert not (tmp_path / "state.json.tmp").exists()


def test_corrupt_file_is_moved_aside(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json")
    st = State(str(path))
    assert st.tx is None and st.outbox == []
    assert not path.exists()
    assert (tmp_path / "state.json.broken").read_text() == "{not json"


def test_partial_file_uses_defaults(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"register_wh": 5}))
    st = State(str(path))
    assert st.register_wh == 5.0 and st.outbox == [] and st.config == {}
    assert os.path.exists(path)
