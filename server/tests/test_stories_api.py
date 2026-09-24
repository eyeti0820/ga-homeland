# -*- coding: utf-8 -*-
"""我们的故事 · P2 冒烟测试（临时库，不碰正式数据）"""
import tempfile, os, json, sys
sys.path.insert(0, "/Users/potato/土小豆/系统代码/GA家园系统/server")
from app.main import create_app
from fastapi.testclient import TestClient

tmp = tempfile.mktemp(suffix=".db")
with TestClient(create_app(db_path=tmp)) as c:
    # 建故事
    res = c.post("/api/stories", json={
        "title": "星港夜航", "background": "架空IF：五人共赴星港号",
        "user_identity": "随船医生", "cast_json": json.dumps(["xiazhou", "qinche"]),
        "style_json": json.dumps({"chapter_words": 1500}), "enabled": 1})
    assert res.status_code == 201, res.text
    sid = res.json()["id"]
    print("create ok:", res.json()["title"], res.json()["cast"], res.json()["style"])
    bad = c.post("/api/stories", json={"title": "x", "cast_json": '["momo"]'})
    print("bad cast ->", bad.status_code, str(bad.json()["detail"])[:50])
    ch1 = c.post(f"/api/stories/{sid}/chapters", json={
        "kind": "chapter", "author_slug": "xiazhou", "title": "启航",
        "content": "A" * 50, "summary": "第一章摘要"})
    print("ch1 ->", ch1.status_code, "idx", ch1.json()["idx"])
    ch2 = c.post(f"/api/stories/{sid}/chapters", json={
        "kind": "chapter", "author_slug": "qinche", "title": "暗流", "content": "B" * 80, "summary": "第二章摘要"})
    print("ch2 idx", ch2.json()["idx"])
    note = c.post(f"/api/stories/{sid}/chapters", json={"kind": "master_note", "content": "主人吐槽：多写点对手戏"})
    seg = c.post(f"/api/stories/{sid}/chapters", json={"kind": "master_seg", "content": "主人插入的正文段", "title": "幕间"})
    print("note idx(应2):", note.json()["idx"], " seg idx(应3):", seg.json()["idx"])
    d = c.get(f"/api/stories/{sid}").json()
    print("chapters:", [(x["kind"], x["idx"]) for x in d["chapters"]], "count:", d["chapter_count"], "by_chars:", d["chapter_by_chars"])
    pn = c.get(f"/api/stories/{sid}/pending-notes").json()
    print("pending notes:", pn["count"])
    cid = pn["notes"][0]["id"]
    c.patch(f"/api/stories/{sid}/chapters/{cid}", json={"consumed": 1})
    print("after consume:", c.get(f"/api/stories/{sid}/pending-notes").json()["count"])
    print("duty xiazhou:", c.get("/api/stories/duty/xiazhou").json()["count"],
          " duty lishen:", c.get("/api/stories/duty/lishen").json()["count"])
    p = c.patch(f"/api/stories/{sid}", json={"enabled": 0, "bible": "圣经v1"}).json()
    print("enabled:", p["enabled"], "duty after off:", c.get("/api/stories/duty/xiazhou").json()["count"])
    print("bible roundtrip:", c.get(f"/api/stories/{sid}").json()["bible"])
    print("del:", c.delete(f"/api/stories/{sid}").status_code, " list:", c.get("/api/stories").json()["count"])
    os.remove(tmp)
    print("ALL OK")
