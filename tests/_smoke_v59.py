"""v1.40 smoke: WebUI diary browsing handler (read-only).

Diaries stored via service.store_diary must be listable/filterable by
channel / persona / day through DiaryHandler, with paging + options.
"""
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hippocampus import MemoryService, MemoryConfig
from page_api_modules import PageApiUtils, DiaryHandler


def _mk(db):
    cfg = MemoryConfig(sqlite_path=db, embedding_name="hash", llm_name="rule")
    cfg.memory_decay_enabled = False
    return MemoryService(cfg)


def _diary(svc, *, summary, channel_id, persona_id, day, chat_type,
           group_id="", group_name="", peer_name=""):
    identity = {
        "session_id": "s", "actor_id": "a", "platform": "p",
        "channel_id": channel_id, "persona_id": persona_id,
        "chat_type": chat_type, "group_id": group_id,
        "group_name": group_name, "peer_name": peer_name,
        "day_label": day,
    }
    return svc.store_diary({"summary": summary, "topics": ["t"],
                            "participants": ["A", "B"]}, identity)


def main():
    fd, db = tempfile.mkstemp(suffix=".db"); os.close(fd)
    svc = _mk(db)
    h = DiaryHandler(PageApiUtils())
    try:
        _diary(svc, summary="group day1 talk", channel_id="g1",
               persona_id="shelly", day="2026-06-20", chat_type="group",
               group_id="708947555", group_name="\u6d4b\u8bd5\u7fa4")
        _diary(svc, summary="group day2 talk", channel_id="g1",
               persona_id="shelly", day="2026-06-21", chat_type="group",
               group_id="708947555", group_name="\u6d4b\u8bd5\u7fa4")
        _diary(svc, summary="private chat", channel_id="p1",
               persona_id="", day="2026-06-21", chat_type="private",
               peer_name="\u5c0f\u660e")

        # options: 2 channels, 2 personas (shelly + ''), 2 days
        opt = h.options(svc)["data"]
        assert len(opt["channels"]) == 2, opt
        assert "shelly" in opt["personas"] and "" in opt["personas"], opt
        assert opt["days"] == ["2026-06-21", "2026-06-20"], opt
        assert opt["total"] == 3, opt
        # channel label uses group name + id
        g1 = [c for c in opt["channels"] if c["channel_id"] == "g1"][0]
        assert "708947555" in g1["label"] and "\u6d4b\u8bd5\u7fa4" in g1["label"], g1
        print("[OK] diary options: channels/personas/days/total")

        # list all
        alld = h.list_diaries(svc)["data"]
        assert alld["total"] == 3 and len(alld["items"]) == 3, alld
        # newest first
        assert alld["items"][0]["day"] in ("2026-06-21",), alld
        print("[OK] diary list returns all, newest first")

        # filter by channel
        byc = h.list_diaries(svc, channel_id="g1")["data"]
        assert byc["total"] == 2, byc
        # filter by persona shelly -> 2
        byp = h.list_diaries(svc, persona_id="shelly")["data"]
        assert byp["total"] == 2, byp
        # filter by no-persona sentinel -> 1 (the private one)
        bynone = h.list_diaries(svc, persona_id="__none__")["data"]
        assert bynone["total"] == 1 and bynone["items"][0]["chat_type"] == "private", bynone
        # filter by day
        byday = h.list_diaries(svc, day="2026-06-20")["data"]
        assert byday["total"] == 1 and byday["items"][0]["summary"] == "group day1 talk", byday
        # text search
        byq = h.list_diaries(svc, q="private")["data"]
        assert byq["total"] == 1, byq
        # combined channel + day
        comb = h.list_diaries(svc, channel_id="g1", day="2026-06-21")["data"]
        assert comb["total"] == 1, comb
        print("[OK] diary filters: channel/persona/none/day/q/combined")

        # paging
        pg = h.list_diaries(svc, k=2, offset=0)["data"]
        assert pg["total"] == 3 and len(pg["items"]) == 2 and pg["k"] == 2, pg
        pg2 = h.list_diaries(svc, k=2, offset=2)["data"]
        assert len(pg2["items"]) == 1 and pg2["offset"] == 2, pg2
        print("[OK] diary paging total/offset/k")

        # detail
        eid = byq["items"][0]["id"]
        det = h.get_detail(svc, eid)["data"]
        assert det["chat_type"] == "private" and det["peer_name"] == "\u5c0f\u660e", det
        assert "content" in det and det["participants"] == ["A", "B"], det
        # non-diary engram rejected
        from hippocampus import Cue  # ensure import path stable
        bad = h.get_detail(svc, "does-not-exist")
        assert bad["status"] == "error", bad
        print("[OK] diary detail + non-diary rejection")

        print("ALL PASS v60-diary")
    finally:
        svc.close()
        try:
            os.remove(db)
        except OSError:
            pass


if __name__ == "__main__":
    main()
