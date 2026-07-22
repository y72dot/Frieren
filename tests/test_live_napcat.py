from __future__ import annotations

import asyncio
import base64
import os
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.live,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.getenv("QQBOT_LIVE") != "1",
        reason="set QQBOT_LIVE=1 to authorize real NapCat/QQ acceptance",
    ),
]


async def test_live_napcat_login_send_and_history_contract():
    from napcat import NapCatClient

    ws_url = os.environ["NAPCAT_WS_URL"]
    token = os.getenv("NAPCAT_TOKEN", "")
    group_id = int(os.environ["QQBOT_LIVE_GROUP_ID"])
    marker = f"[qqbot live acceptance {int(time.time())}]"

    async with NapCatClient(ws_url, token) as client:
        login = await client.get_login_info()
        assert isinstance(login, dict) and login
        sent = await client.send_group_msg(group_id=group_id, message=marker)
        assert isinstance(sent, dict)
        for _ in range(15):
            history = await client.get_group_msg_history(group_id=group_id, count=20)
            if marker in str(history):
                break
            await asyncio.sleep(1)
        else:
            pytest.fail("sent message did not appear in group history within 15s")

        artifact = os.getenv("QQBOT_LIVE_ARTIFACT")
        if artifact:
            path = Path(artifact)
            assert path.is_file(), f"QQBOT_LIVE_ARTIFACT is not a file: {path}"
            size = path.stat().st_size
            assert size <= 10 * 1024 * 1024, "live acceptance artifact exceeds 10 MiB"
            payload = "base64://" + base64.b64encode(path.read_bytes()).decode("ascii")
            remote_name = f"qqbot-l6-{int(time.time())}-{path.name}"
            uploaded = await client.upload_group_file(
                group_id=group_id,
                file=payload,
                name=remote_name,
            )
            assert isinstance(uploaded, dict)
            for _ in range(20):
                files = await client.get_group_root_files(group_id=group_id)
                if remote_name in str(files):
                    break
                await asyncio.sleep(1)
            else:
                pytest.fail("uploaded file did not appear in group files within 20s")
