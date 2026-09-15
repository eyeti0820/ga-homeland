"""M0 冒烟：真启 uvicorn 子进程，用 stdlib 轮询 API 验证全链路（不依赖 TestClient，
避开 httpx 0.28 与 starlette 0.35 的兼容坑）。

用法：cd server && python3 tests/test_smoke.py   （exit code 即结果）
"""
import json
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

from app import config  # noqa: E402

PORT = 7899  # 冒烟专用口，避开正式 7842
BASE = f"http://127.0.0.1:{PORT}"
EXPECTED_TABLES = 14  # 开发方案 §6 + diary 两表


def get(path: str):
    try:
        with urllib.request.urlopen(BASE + path, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def get_raw(path: str):
    with urllib.request.urlopen(BASE + path, timeout=5) as resp:
        return resp.status, resp.read().decode()


def post(path: str, payload: dict):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def delete(path: str):
    req = urllib.request.Request(BASE + path, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        db = Path(td) / "smoke.db"
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(PORT)],
            cwd=SERVER_DIR,
            env={**dict(__import__("os").environ),
                 "HOMESTEAD_DB": str(db),  # 冒烟用临时库，不碰正式 homestead.db
                 },
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            # 轮询等待启动
            deadline = time.time() + 20
            body = None
            while time.time() < deadline:
                if proc.poll() is not None:
                    print("uvicorn died early:\n", proc.stdout.read()[-2000:])
                    return 1
                try:
                    status, body = get("/api/health")
                    if status == 200:
                        break
                except Exception:
                    time.sleep(0.3)
            else:
                print("timeout waiting for", BASE)
                return 1

            assert body["ok"] is True, body
            assert body["db_tables"] == EXPECTED_TABLES, body

            status, actors = get("/api/actors")
            assert status == 200 and actors == [], actors

            status, _ = get("/api/actors/999")
            assert status == 404, status

            # ---------- M1 便签墙 ----------
            # 1. 种子（幂等跑两遍）
            seed = SERVER_DIR.parent / "seeds" / "seed_actors.py"
            for _ in range(2):
                r = subprocess.run([sys.executable, str(seed), "--db", str(db)],
                                   capture_output=True, text=True)
                assert r.returncode == 0, r.stdout + r.stderr
            status, actors = get("/api/actors")
            assert status == 200 and len(actors) == 7, (status, actors)
            master = next(a for a in actors if a["type"] == "master")
            xiazhou = next(a for a in actors if a["name"] == "夏以昼")
            assert master["name"] == "小墨", master

            # 2. 主人发便签
            status, note = post("/api/notes", {
                "actor_id": master["id"], "content": "今晚想吃糖醋排骨",
                "color": "butter", "pos_x": 0.3, "pos_y": 0.2})
            assert status == 201 and note["author_name"] == "小墨", (status, note)
            nid = note["id"]

            # 3. 角色回复
            status, rep = post(f"/api/notes/{nid}/replies", {
                "actor_id": xiazhou["id"], "content": "糖醋排骨？好啊，晚上做给你。"})
            assert status == 201 and rep["author_name"] == "夏以昼", (status, rep)

            # 4. 主人再回复 → unread pending
            status, _ = post(f"/api/notes/{nid}/replies", {
                "actor_id": master["id"], "content": "那我等你呀"})
            assert status == 201, status
            status, un = get(f"/api/unread?actor_id={xiazhou['id']}&status=pending")
            assert status == 200 and un["count"] == 1, un
            assert un["items"][0]["ref"]["note_id"] == nid, un
            uid = un["items"][0]["id"]

            # 5. 待回复队列（主人的便签没有角色回过）
            status, pend = post("/api/notes", {
                "actor_id": master["id"], "content": "周末去海边吗", "color": "mist"})
            assert status == 201, status
            status, q = get(f"/api/notes/pending-reply/{xiazhou['id']}?limit=10")
            assert status == 200 and q["count"] >= 1, q

            # 6. unread 状态流转
            status, done = post(f"/api/unread/{uid}/done", {})
            assert status == 200 and done["status"] == "done", done
            status, un2 = get(f"/api/unread?actor_id={xiazhou['id']}&status=pending")
            assert un2["count"] == 0, un2

            # 6b. 游标分页（M1.5 补丁）：再造 4 张 → 共 6 张
            for i in range(4):
                status, _ = post("/api/notes", {
                    "actor_id": master["id"], "content": f"分页测试 {i}",
                    "color": "sage"})
                assert status == 201, status
            status, p1 = get("/api/notes?limit=2")
            assert p1["count"] == 2 and p1["has_more"] is True, p1
            assert p1["notes"][0]["id"] > p1["notes"][1]["id"], "默认应 id DESC"
            floor = p1["notes"][-1]["id"]
            status, p2 = get(f"/api/notes?limit=2&before_id={floor}")
            assert p2["count"] == 2 and p2["notes"][0]["id"] < floor, p2
            after = p2["notes"][0]["id"]
            status, p3 = get(f"/api/notes?limit=50&after_id={after}")
            assert p3["count"] == 2 and p3["notes"][0]["id"] < p3["notes"][-1]["id"], \
                "after_id 应返回更新的且 id ASC"
            assert p3["notes"][-1]["id"] == p1["notes"][0]["id"], "增量窗口应覆盖最新"
            status, pfull = get("/api/notes?limit=50")
            assert pfull["count"] == 6 and pfull["has_more"] is False, pfull

            # 6c. 交换日记（M1.5）：写页 → 回信 → unread 流转 → 翻回信
            status, de = post("/api/diary/entries", {
                "actor_id": xiazhou["id"], "content": "今晚的训练场很安静……（日记页1）",
                "mood": "安静"})
            assert status == 201 and de["author_name"] == "夏以昼" and de["mood"] == "安静", de
            status, de2 = post("/api/diary/entries", {
                "actor_id": xiazhou["id"], "content": "给主人留了一块桂花糕（日记页2）"})
            assert status == 201, status
            status, drep = post(f"/api/diary/entries/{de['id']}/replies", {
                "actor_id": master["id"], "content": "桂花糕收到了，谢谢小昼。"})
            assert status == 201 and drep["unread_created"] is True, drep
            status, drlist = get(f"/api/diary/pending-replies/{xiazhou['id']}")
            assert drlist["count"] == 1 and drlist["replies"][0]["entry_id"] == de["id"], drlist
            status, du = get(f"/api/unread?actor_id={xiazhou['id']}&status=pending")
            assert du["count"] == 1 and du["items"][0]["kind"] == "master_diary_reply", du
            assert du["items"][0]["ref"]["entry_id"] == de["id"], du
            status, _ = post(f"/api/unread/{du['items'][0]['id']}/done", {})
            assert status == 200, status
            status, drlist2 = get(f"/api/diary/pending-replies/{xiazhou['id']}")
            assert drlist2["count"] == 0, drlist2
            # 列表：同角色过滤 + 回信内嵌 + 游标
            status, dl = get(f"/api/diary/entries?actor_id={xiazhou['id']}")
            assert dl["count"] == 2 and dl["entries"][0]["id"] > dl["entries"][1]["id"], dl
            assert dl["entries"][-1]["replies"][0]["author_name"] == "小墨", dl
            status, done = get(f"/api/diary/entries/{de['id']}")
            assert done["replies"][0]["content"].startswith("桂花糕"), done

            # 7. 墙上同屏可见 + 静态页
            status, wall = get("/api/notes?limit=50")
            assert wall["count"] == 6, wall
            with_replies = [n for n in wall["notes"] if n["replies"]]
            assert with_replies and with_replies[0]["replies"][-1]["author_name"] in ("夏以昼", "小墨")
            status, html = get_raw("/")
            assert status == 200 and "便签墙" in html, status

            # 8. 朋友圈（M2）：发圈 → 评论(顶层+楼中楼) → unread → 点赞 → 流
            status, mp1 = post("/api/posts", {
                "actor_id": xiazhou["id"], "content": "试飞新航线，晚霞满分。（M2冒烟圈）"})
            assert status == 201 and mp1["author_name"] == "夏以昼", mp1
            status, mc1 = post(f"/api/posts/{mp1['id']}/comments", {
                "actor_id": master["id"], "content": "航线图发我看看"})
            assert status == 201 and mc1["author_type"] == "master", mc1
            # 楼中楼：角色回复主人的评论
            status, mc2 = post(f"/api/posts/{mp1['id']}/comments", {
                "actor_id": xiazhou["id"], "content": "落地就发。",
                "parent_id": mc1["id"]})
            assert status == 201 and mc2["parent_id"] == mc1["id"], mc2
            # 主人评论 → 圈主收到 master_comment unread（ref 带 post_id/content）
            status, mu = get(f"/api/unread?actor_id={xiazhou['id']}&status=pending")
            assert mu["count"] == 1 and mu["items"][0]["kind"] == "master_comment", mu
            assert mu["items"][0]["ref"]["post_id"] == mp1["id"], mu
            assert mu["items"][0]["ref"]["content"] == "航线图发我看看", mu
            status, _ = post(f"/api/unread/{mu['items'][0]['id']}/done", {})
            assert status == 200, status
            # 点赞：重复赞 409，DELETE 取消（query 参数）
            status, lk = post(f"/api/posts/{mp1['id']}/likes", {"actor_id": master["id"]})
            assert status == 201 and lk["liked"] is True and lk["like_count"] == 1, lk
            status, dup = post(f"/api/posts/{mp1['id']}/likes", {"actor_id": master["id"]})
            assert status == 409, dup
            status, unlk = delete(f"/api/posts/{mp1['id']}/likes?actor_id={master['id']}")
            assert status == 200 and unlk["liked"] is False and unlk["like_count"] == 0, unlk
            # 信息流：最新在前 + comments/likes 内嵌 + 游标（冒烟库仅 M2 两条圈）
            xinghui = next(a for a in actors if a["name"] == "沈星回")
            status, _ = post("/api/posts", {
                "actor_id": xinghui["id"], "content": "落地报告：全程平稳。（M2冒烟圈2）"})
            status, fl = get("/api/posts?limit=2")
            assert status == 200 and fl["count"] == 2, fl
            assert fl["posts"][0]["author_name"] == "沈星回", fl
            emb = fl["posts"][0]
            assert len(emb["comments"]) == 0 and emb["likes"] == [], emb
            status, older = get(f"/api/posts?limit=2&before_id={fl['posts'][0]['id']}")
            assert older["count"] == 1 and older["posts"][0]["id"] == mp1["id"], older
            assert older["posts"][0]["comments"][-1]["parent_id"] == mc1["id"], older
            assert any(c["author_name"] == "小墨" for c in older["posts"][0]["comments"]), older
            # 静态页
            status, mhtml = get_raw("/moments.html")
            assert status == 200 and "朋友圈" in mhtml, status

            # 9. 论坛（M3）：种子 → 板/马甲 → 起楼(置顶权限) → 楼层 → 游标 → 马甲隐私
            seed_forum = SERVER_DIR.parent / "seeds" / "seed_forum.py"
            for _ in range(2):  # 幂等跑两遍
                subprocess.run([sys.executable, str(seed_forum), "--db", str(db)],
                               check=True, capture_output=True)
            status, fb = get("/api/forum/boards")
            assert status == 200 and fb["count"] == 6, fb
            assert next(b for b in fb["items"] if b["key"] == "darkspot")["access"] == "secret", fb
            status, masks = get("/api/forum/masks")
            assert status == 200 and masks["count"] == 31, masks  # 7 actors + 24 NPC
            npc_mask = next(m for m in masks["items"] if m["actor_type"] == "npc")
            char_mask = next(m for m in masks["items"] if m["actor_name"] == "夏以昼")
            mei_mask = next(m for m in masks["items"] if m["actor_type"] == "mephisto")
            # 起楼：NPC ok；角色马甲置顶 403；小梅编辑部置顶 ok
            status, th1 = post("/api/forum/threads", {
                "board_key": "city", "mask_id": npc_mask["id"],
                "title": "巷口流浪猫带崽了（M3冒烟）",
                "content": "三只，一只橘子两只奶牛，蹲点投喂的报个名。"})
            assert status == 201 and th1["author"] == npc_mask["forum_username"], th1
            status, no_pin = post("/api/forum/threads", {
                "board_key": "hunters", "mask_id": char_mask["id"],
                "title": "不该被置顶", "content": "x", "is_pinned": True})
            assert status == 403, no_pin
            status, th2 = post("/api/forum/threads", {
                "board_key": "city", "mask_id": mei_mask["id"],
                "title": "【编辑部】临空FM 第0期", "content": "试刊，嘎。",
                "is_pinned": True})
            assert status == 201 and th2["id"] > th1["id"], th2
            # 楼层 + 详情（views 自增、楼层 ASC）
            status, fl1 = post(f"/api/forum/threads/{th1['id']}/posts", {
                "mask_id": char_mask["id"], "content": "求坐标，周末去看看。（M3冒烟楼层）"})
            assert status == 201 and fl1["author"] == char_mask["forum_username"], fl1
            status, detail = get(f"/api/forum/threads/{th1['id']}")
            assert status == 200 and detail["posts"][0]["id"] == fl1["id"], detail
            assert detail["thread"]["views"] >= 1 and detail["thread"]["author"] == npc_mask["forum_username"], detail
            # 置顶在前 + 游标翻页
            status, tl = get("/api/forum/threads?board_key=city&limit=1")
            assert status == 200 and tl["count"] == 1 and tl["has_more"] is True, tl
            assert tl["threads"][0]["id"] == th2["id"] and tl["threads"][0]["is_pinned"] == 1, tl
            status, page2 = get(f"/api/forum/threads?board_key=city&limit=1&before_id={th2['id']}")
            assert status == 200 and page2["has_more"] is False, page2
            assert page2["threads"][0]["id"] == th1["id"], page2
            assert page2["threads"][0]["reply_count"] == 1, page2
            # 马甲隐私红线：展示型接口绝不出现 actor 对照/真名
            raw = json.dumps(tl) + json.dumps(page2) + json.dumps(detail)
            assert "actor_id" not in raw and "actor_name" not in raw, "展示层泄漏 actor 对照"
            for real in ("夏以昼", "沈星回", "秦彻", "黎深", "祁煜", "小墨", "小梅"):
                assert real not in raw, f"展示层泄漏真名 {real}"
            # 404 家族
            status, _ = get("/api/forum/threads?board_key=nope")
            assert status == 404, status
            status, _ = get("/api/forum/threads/999")
            assert status == 404, status
            status, _ = post("/api/forum/threads", {
                "board_key": "city", "mask_id": 999, "title": "t", "content": "c"})
            assert status == 404, status

            print(f"SMOKE OK: health(tables={EXPECTED_TABLES}) / actors(7+幂等) / "
                  f"notes CRUD / replies / unread pending→done / pending-reply / "
                  f"diary 页+回信+unread流转 / moments 发圈+评论+楼中楼+unread+点赞+游标 / "
                  f"forum 六板+马甲31+置顶权限+楼层+游标+隐私 / static 全过")
            return 0
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    sys.exit(main())
