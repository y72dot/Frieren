from __future__ import annotations

import os
import time

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
        history = await client.get_group_msg_history(group_id=group_id, count=20)
        assert marker in str(history)

        artifact = os.getenv("QQBOT_LIVE_ARTIFACT")
        if artifact:
            uploaded = await client.upload_group_file(
                group_id=group_id,
                file=artifact,
                name=os.path.basename(artifact),
            )
            assert isinstance(uploaded, dict)
