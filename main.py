import asyncio
import aiohttp
import websockets
import discord
import os
import json
import time
import re
import base64
import io
import uuid
import hashlib
import math
import random
import traceback
from dotenv import load_dotenv

load_dotenv()

TOKEN   = os.getenv('DISCORD_TOKEN')
TOKEN_2 = os.getenv('DISCORD_TOKEN_2')
TOKEN_3 = os.getenv('DISCORD_TOKEN_3')
ALLOWED_USER_IDS = set(filter(None, [
    (os.getenv('ALLOWED_USER_ID') or '').strip(),
    (os.getenv('ALLOWED_USER_ID_2') or '').strip(),
    (os.getenv('ALLOWED_USER_ID_3') or '').strip(),
]))

# ─── Logging ───────────────────────────────────────────────────────────────────

def log_ok(msg):  print(f'\033[32m[+] {msg}\033[0m', flush=True)
def log_err(msg): print(f'\033[31m[-] {msg}\033[0m', flush=True)
def log_warn(msg):print(f'\033[33m[!] {msg}\033[0m', flush=True)
def log_inf(msg): print(f'\033[36m[i] {msg}\033[0m', flush=True)

async def sleep_ms(ms):
    await asyncio.sleep(ms / 1000)

# ─── HTTP helpers ──────────────────────────────────────────────────────────────

_session = None

async def get_session():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession()
    return _session

async def download_bytes(url, token=None, redirects=0):
    if redirects > 5:
        raise Exception('Too many redirects')
    s = await get_session()
    headers = {'User-Agent': 'Mozilla/5.0'}
    if token:
        headers['Authorization'] = token
    try:
        async with s.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=20), allow_redirects=True) as r:
            if r.status != 200:
                raise Exception(f'HTTP {r.status}')
            data = await r.read()
            mime = r.headers.get('content-type', 'image/png')
            return data, mime
    except asyncio.TimeoutError:
        raise Exception('Download timeout')

async def download_b64(url):
    data, mime = await download_bytes(url)
    return f'data:{mime};base64,{base64.b64encode(data).decode()}'

async def api(method, path, body=None, form=None, retries=0, token=None, base='https://discord.com/api/v10'):
    tok = token or TOKEN
    s = await get_session()
    headers = {'Authorization': tok, 'User-Agent': 'Mozilla/5.0'}
    timeout = aiohttp.ClientTimeout(total=20)
    try:
        if form:
            async with s.request(method, base + path, headers=headers, data=form, timeout=timeout) as r:
                return await _handle(r, method, path, body, form, retries, token)
        else:
            headers['Content-Type'] = 'application/json'
            async with s.request(method, base + path, headers=headers, json=body, timeout=timeout) as r:
                return await _handle(r, method, path, body, form, retries, token)
    except asyncio.TimeoutError:
        raise Exception('API request timeout')

async def _handle(r, method, path, body, form, retries, token):
    if r.status == 204:
        return None
    if r.status == 429 and retries < 3:
        retry_after = float(r.headers.get('retry-after', 5))
        await asyncio.sleep(min(retry_after + 0.5, 30))
        return await api(method, path, body, form, retries + 1, token)
    if r.status < 300:
        try:
            return await r.json(content_type=None)
        except Exception:
            return await r.text()
    txt = await r.text()
    raise Exception(f'API {r.status}: {txt}')

# ─── QUEST logic ───────────────────────────────────────────────────────────────

USER_AGENT_Q       = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) discord/1.0.9236 Chrome/138.0.7204.251 Electron/37.6.0 Safari/537.36'
USER_AGENT_ANDROID = 'Discord-Android/316011;RNA'
BUILD_NUMBER       = [539951]

CLIENT_PROPS = {
    'os': 'Windows', 'browser': 'Discord Client', 'release_channel': 'stable',
    'client_version': '1.0.9236', 'os_version': '10.0.19045', 'os_arch': 'x64',
    'app_arch': 'x64', 'system_locale': 'en-US', 'has_client_mods': False,
    'browser_user_agent': USER_AGENT_Q, 'browser_version': '37.6.0',
    'os_sdk_version': '19045', 'native_build_number': 81687,
    'client_event_source': None, 'client_app_state': 'focused',
}
ANDROID_PROPS = {
    'os': 'Android', 'browser': 'Discord Android', 'device': 'b0q',
    'system_locale': 'en-US', 'has_client_mods': False,
    'client_version': '316.11 - rn', 'release_channel': 'googleRelease',
    'device_vendor_id': str(uuid.uuid4()), 'design_id': 2,
    'browser_user_agent': '', 'browser_version': '', 'os_version': '28',
    'client_build_number': 5169, 'client_event_source': None,
    'launch_signature': '1771754995045142953', 'client_app_state': 'active',
}

def get_super_props(android=False):
    if android:
        props = {**ANDROID_PROPS, 'client_launch_id': str(uuid.uuid4()),
                 'client_heartbeat_session_id': str(uuid.uuid4())}
    else:
        props = {**CLIENT_PROPS, 'client_build_number': BUILD_NUMBER[0],
                 'client_launch_id': str(uuid.uuid4()), 'launch_signature': str(uuid.uuid4()),
                 'client_heartbeat_session_id': str(uuid.uuid4())}
    return base64.b64encode(json.dumps(props).encode()).decode()

async def fetch_build_number():
    s = await get_session()
    try:
        async with s.get('https://discord.com/app', headers={'User-Agent': USER_AGENT_Q}, timeout=aiohttp.ClientTimeout(total=10)) as r:
            html = await r.text()
        scripts = re.findall(r'/assets/web\.[a-f0-9]+\.js', html)[:5]
        for sp in scripts:
            try:
                async with s.get(f'https://discord.com{sp}', headers={'User-Agent': USER_AGENT_Q}, timeout=aiohttp.ClientTimeout(total=10)) as r2:
                    js = await r2.text()
                m = re.search(r'buildNumber["\s:]+["]*(\d{5,7})', js)
                if m:
                    BUILD_NUMBER[0] = int(m.group(1))
                    print(f'📦 Discord build number: {m.group(1)}', flush=True)
                    return
            except Exception:
                pass
    except Exception:
        pass

async def quest_req(method, path, tok, body=None, android=False, extra_headers=None):
    s = await get_session()
    ua = USER_AGENT_ANDROID if android else USER_AGENT_Q
    headers = {
        'Authorization': tok,
        'Content-Type': 'application/json',
        'User-Agent': ua,
        'X-Super-Properties': get_super_props(android),
        'X-Discord-Locale': 'en-US',
        'X-Debug-Options': 'bugReporterEnabled',
        'Accept-Language': 'en-US',
    }
    if not android:
        headers['Origin'] = 'https://discord.com'
        headers['Referer'] = 'https://discord.com/channels/@me'
    if extra_headers:
        headers.update(extra_headers)
    timeout = aiohttp.ClientTimeout(total=20)
    async with s.request(method, f'https://discord.com/api/v10{path}',
                         headers=headers, json=body, timeout=timeout) as r:
        if r.status == 204:
            return None
        if r.status == 429:
            d = await r.json(content_type=None)
            retry = d.get('retry_after', 5)
            await asyncio.sleep(float(retry) + 0.5)
            raise Exception(f'Rate limited: retry after {retry}s')
        if r.status < 300:
            return await r.json(content_type=None)
        txt = await r.text()
        raise Exception(f'{r.status}: {txt}')

async def claim_quest_reward(tok, quest, label):
    name = quest['config']['messages']['quest_name']
    platforms = (quest.get('config', {}).get('rewards_config') or {}).get('platforms') or []
    if not platforms:
        return
    try:
        res = await quest_req('POST', f'/quests/{quest["id"]}/claim-reward', tok, {
            'platform': platforms[0],
            'location': 11,
            'is_targeted': False,
            'metadata_raw': None,
            'metadata_sealed': None,
            'traffic_metadata_raw': quest.get('traffic_metadata_raw'),
            'traffic_metadata_sealed': quest.get('traffic_metadata_sealed'),
        })
        if res:
            quest['user_status'] = res
        print(f'[{label}] 🎁 Reward claimed for "{name}"!', flush=True)
    except Exception as e:
        print(f'[{label}] ⚠️ Claim reward "{name}": {e}', flush=True)

async def do_video_quest(tok, quest, label):
    name = quest['config']['messages']['quest_name']
    tasks = quest['config']['task_config_v2']['tasks']
    is_android = 'WATCH_VIDEO_ON_MOBILE' in tasks and 'WATCH_VIDEO' not in tasks
    task_key = 'WATCH_VIDEO_ON_MOBILE' if is_android else 'WATCH_VIDEO'
    needed = tasks[task_key]['target']
    user_status = quest.get('user_status') or {}
    done = user_status.get('progress', {}).get(task_key, {}).get('value', 0)
    enrolled_at_str = user_status.get('enrolled_at') or '2000-01-01T00:00:00'
    enrolled_at = time.mktime(time.strptime(enrolled_at_str[:19], '%Y-%m-%dT%H:%M:%S')) * 1000
    speed, interval = 7, 7
    completed = False
    print(f'[{label}] 🎬 Video quest "{name}" (~{math.ceil((needed - done) / 60)} min)...', flush=True)
    while done < needed:
        max_allowed = (time.time() * 1000 - enrolled_at) / 1000 + 10
        if max_allowed - done >= speed:
            try:
                timestamp = min(needed, done + speed) + random.random()
                res = await quest_req('POST', f'/quests/{quest["id"]}/video-progress', tok,
                                      {'timestamp': timestamp}, android=is_android)
                done = min(needed, done + speed)
                if res and res.get('completed_at'):
                    completed = True
                    break
            except Exception as e:
                print(f'[{label}] ❌ Video progress: {e}', flush=True)
                await asyncio.sleep(15)
        if done >= needed:
            break
        await asyncio.sleep(interval)
    if not completed:
        try:
            await quest_req('POST', f'/quests/{quest["id"]}/video-progress', tok,
                            {'timestamp': needed}, android=is_android)
        except Exception as e:
            print(f'[{label}] ❌ Final video: {e}', flush=True)
    print(f'[{label}] ✅ Quest "{name}" done!', flush=True)
    await claim_quest_reward(tok, quest, label)

async def do_play_quest(tok, quest, label):
    name = quest['config']['messages']['quest_name']
    tasks = quest['config']['task_config_v2']['tasks']
    task_key = next(k for k in ('PLAY_ON_DESKTOP', 'PLAY_ON_XBOX', 'PLAY_ON_PLAYSTATION') if k in tasks)
    needed = tasks[task_key]['target']
    app_id = quest['config']['application']['id']
    app_name = quest['config']['application']['name']
    user_status = quest.get('user_status') or {}
    done = user_status.get('progress', {}).get(task_key, {}).get('value', 0)
    print(f'[{label}] 🎮 Play quest "{name}" (~{math.ceil((needed - done) / 60)} min)...', flush=True)
    while True:
        us = quest.get('user_status') or {}
        if us.get('completed_at'):
            break
        done = (us.get('progress') or {}).get(task_key, {}).get('value', done)
        if done >= needed:
            break
        try:
            res = await quest_req('POST', f'/quests/{quest["id"]}/heartbeat', tok,
                                  {'application_id': app_id, 'terminal': False})
            if res:
                quest['user_status'] = res
                done = (res.get('progress') or {}).get(task_key, {}).get('value', done)
                if res.get('completed_at'):
                    break
            print(f'[{label}] 🎮 {app_name}: {math.ceil((needed - done) / 60)} min left', flush=True)
        except Exception as e:
            print(f'[{label}] ❌ Heartbeat: {e}', flush=True)
            await asyncio.sleep(30)
            continue
        await asyncio.sleep(20)
    try:
        res = await quest_req('POST', f'/quests/{quest["id"]}/heartbeat', tok,
                              {'application_id': app_id, 'terminal': True})
        if res:
            quest['user_status'] = res
    except Exception:
        pass
    print(f'[{label}] ✅ Quest "{name}" done!', flush=True)
    await claim_quest_reward(tok, quest, label)

async def do_activity_quest(tok, quest, label):
    name = quest['config']['messages']['quest_name']
    tasks = quest['config']['task_config_v2']['tasks']
    task_key = 'PLAY_ACTIVITY'
    needed = tasks[task_key]['target']
    app_id = quest['config']['application']['id']
    app_name = quest['config']['application']['name']
    stream_key = 'call:1:1'
    user_status = quest.get('user_status') or {}
    done = user_status.get('progress', {}).get(task_key, {}).get('value', 0)
    print(f'[{label}] 🎯 Activity quest "{name}" (~{math.ceil((needed - done) / 60)} min)...', flush=True)
    while True:
        us = quest.get('user_status') or {}
        if us.get('completed_at'):
            break
        done = (us.get('progress') or {}).get(task_key, {}).get('value', done)
        if done >= needed:
            break
        try:
            res = await quest_req('POST', f'/quests/{quest["id"]}/heartbeat', tok,
                                  {'stream_key': stream_key, 'terminal': False})
            if res:
                quest['user_status'] = res
                done = (res.get('progress') or {}).get(task_key, {}).get('value', done)
                if res.get('completed_at'):
                    break
            print(f'[{label}] 🎯 {app_name}: {math.ceil((needed - done) / 60)} min left', flush=True)
        except Exception as e:
            print(f'[{label}] ❌ Activity heartbeat: {e}', flush=True)
            await asyncio.sleep(30)
            continue
        await asyncio.sleep(20)
    try:
        res = await quest_req('POST', f'/quests/{quest["id"]}/heartbeat', tok,
                              {'stream_key': stream_key, 'terminal': True})
        if res:
            quest['user_status'] = res
    except Exception:
        pass
    print(f'[{label}] ✅ Quest "{name}" done!', flush=True)
    await claim_quest_reward(tok, quest, label)

async def do_achievement_quest(tok, quest, label):
    name = quest['config']['messages']['quest_name']
    app_id = quest['config']['application']['id']
    app_name = quest['config']['application']['name']
    target = quest['config']['task_config_v2']['tasks']['ACHIEVEMENT_IN_ACTIVITY']['target']
    print(f'[{label}] 🏆 Achievement quest "{name}" ({app_name})...', flush=True)
    s = await get_session()
    try:
        # 1. OAuth2 authorize
        params = 'response_type=code&client_id={}&scope=identify%20applications.commands%20applications.entitlements&state='.format(app_id)
        auth_res = await quest_req('POST', f'/oauth2/authorize?{params}', tok, {
            'permissions': '0', 'authorize': True, 'integration_type': 1,
            'location_context': {'guild_id': '10000', 'channel_id': '10000', 'channel_type': 10000},
        })
        location = (auth_res or {}).get('location', '')
        from urllib.parse import urlparse, parse_qs
        auth_code = parse_qs(urlparse(location).query).get('code', [None])[0]
        if not auth_code:
            print(f'[{label}] ❌ No OAuth code for "{name}"', flush=True)
            return
        # 2. Get proxy ticket → referrer
        ticket_res = await quest_req('POST', f'/applications/{app_id}/proxy-tickets', tok, {})
        ticket = (ticket_res or {}).get('ticket', '')
        if not ticket:
            print(f'[{label}] ❌ No proxy ticket for "{name}"', flush=True)
            return
        referrer = f'https://{app_id}.discordsays.com/?instance_id=example-cl-instance&platform=desktop&discord_proxy_ticket={ticket}'
        # 3. Authorize with discordsays
        desktop_headers = {
            'Content-Type': 'application/json', 'X-Auth-Token': '',
            'X-Discord-Quest-ID': quest['id'], 'Referer': referrer,
            'User-Agent': USER_AGENT_Q,
        }
        async with s.post(f'https://{app_id}.discordsays.com/.proxy/acf/authorize',
                          json={'code': auth_code}, headers=desktop_headers,
                          timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status >= 400:
                print(f'[{label}] ❌ discordsays auth failed: {r.status}', flush=True)
                return
            act_data = await r.json(content_type=None)
        act_token = act_data.get('token', '')
        if not act_token:
            print(f'[{label}] ❌ No activity token for "{name}"', flush=True)
            return
        # 4. Progress the quest
        desktop_headers['X-Auth-Token'] = act_token
        async with s.post(f'https://{app_id}.discordsays.com/.proxy/acf/quest/progress',
                          json={'progress': target}, headers=desktop_headers,
                          timeout=aiohttp.ClientTimeout(total=20)) as r2:
            if r2.status >= 400:
                print(f'[{label}] ❌ discordsays progress failed: {r2.status}', flush=True)
                return
        # 5. Revoke OAuth token
        try:
            tokens = await quest_req('GET', '/oauth2/tokens', tok)
            if tokens:
                for t in tokens:
                    if (t.get('application') or {}).get('id') == app_id:
                        await quest_req('DELETE', f'/oauth2/tokens/{t["id"]}', tok)
                        break
        except Exception:
            pass
        print(f'[{label}] ✅ Quest "{name}" done!', flush=True)
        await claim_quest_reward(tok, quest, label)
    except Exception as e:
        print(f'[{label}] ❌ Achievement quest "{name}": {e}', flush=True)

async def run_auto_quest(tok, label):
    print(f'[{label}] 🔍 Checking quests...', flush=True)
    try:
        resp = await quest_req('GET', '/quests/@me', tok)
        if not resp:
            print(f'[{label}] 📋 No quests right now.', flush=True)
            return
        if isinstance(resp, list):
            all_quests = list(resp)
            excluded = []
        else:
            if resp.get('quest_enrollment_blocked_until'):
                print(f'[{label}] ⏳ Quests blocked until {resp["quest_enrollment_blocked_until"]}', flush=True)
                return
            all_quests = list(resp.get('quests') or [])
            excluded = resp.get('excluded_quests') or []
        # Fetch excluded quests individually (they may still be completable)
        for ex in excluded:
            ex_id = ex.get('id') if isinstance(ex, dict) else ex
            if not ex_id:
                continue
            try:
                q_data = await quest_req('GET', f'/quests/{ex_id}', tok)
                if q_data:
                    q_data.setdefault('user_status', None)
                    all_quests.append(q_data)
            except Exception:
                pass
        now = time.time()
        quests = [q for q in all_quests
                  if not (q.get('user_status') or {}).get('completed_at')
                  and time.mktime(time.strptime(
                      (q.get('config') or {}).get('expires_at', '2099-01-01T00:00:00')[:19],
                      '%Y-%m-%dT%H:%M:%S')) > now]
        if not quests:
            print(f'[{label}] 📋 No incomplete quests right now.', flush=True)
            return
        print(f'[{label}] 📋 {len(quests)} quest(s) to do.', flush=True)
        for quest in quests:
            name = (quest.get('config') or {}).get('messages', {}).get('quest_name', quest.get('id', '?'))
            tasks = (quest.get('config') or {}).get('task_config_v2', {}).get('tasks', {})
            if not tasks:
                continue
            is_android = 'WATCH_VIDEO_ON_MOBILE' in tasks and 'WATCH_VIDEO' not in tasks
            if not (quest.get('user_status') or {}).get('enrolled_at'):
                print(f'[{label}] 📝 Enrolling in "{name}"...', flush=True)
                try:
                    result = await quest_req('POST', f'/quests/{quest["id"]}/enroll', tok, {
                        'location': 12 if is_android else 11,
                        'is_targeted': False,
                        'metadata_sealed': None,
                        'traffic_metadata_raw': quest.get('traffic_metadata_raw'),
                        'traffic_metadata_sealed': quest.get('traffic_metadata_sealed'),
                    }, android=is_android, extra_headers={'AndroidRequest': 'true' if is_android else 'false'})
                    quest['user_status'] = result or {}
                    await asyncio.sleep(3)
                except Exception as e:
                    print(f'[{label}] ❌ Enroll "{name}": {e}', flush=True)
                    continue
            if 'WATCH_VIDEO' in tasks or 'WATCH_VIDEO_ON_MOBILE' in tasks:
                await do_video_quest(tok, quest, label)
            elif any(k in tasks for k in ('PLAY_ON_DESKTOP', 'PLAY_ON_XBOX', 'PLAY_ON_PLAYSTATION')):
                await do_play_quest(tok, quest, label)
            elif 'PLAY_ACTIVITY' in tasks:
                await do_activity_quest(tok, quest, label)
            elif 'ACHIEVEMENT_IN_ACTIVITY' in tasks:
                await do_achievement_quest(tok, quest, label)
            elif 'STREAM_ON_DESKTOP' in tasks:
                print(f'[{label}] ⚠️ "{name}" requires real streaming — skip.', flush=True)
            else:
                print(f'[{label}] ⚠️ "{name}" ({list(tasks.keys())}) unsupported — skip.', flush=True)
            await asyncio.sleep(5)
    except Exception as e:
        print(f'[{label}] ❌ Quest check failed: {e}', flush=True)

# ─── BUMPER ────────────────────────────────────────────────────────────────────

DISBOARD_ID = '302050872383242240'

RICH_PRESENCE = {
    'applicationId': '1514543367407534091',
    'name': 'Shockify',
    'details': 'Best Roblox beaming server Rn',
    'state': 'discord.gg/FPfu6PN4Cn',
    'largeImage': '66642', 'largeText': 'Shockify',
    'smallImage': '66598', 'smallText': 'Shockify',
    'buttons': [{'label': 'Join now', 'url': 'https://discord.gg/FPfu6PN4Cn'}]
}

BUMP_ACCOUNTS = [a for a in [
    {'token': TOKEN,   'channel_id': '1474360829691302069', 'label': 'Account 1', 'start_delay': 0},
    {'token': TOKEN_2, 'channel_id': '1474360829691302069', 'label': 'Account 2', 'start_delay': 60},
    {'token': TOKEN_3, 'channel_id': '1474360829691302069', 'label': 'Account 3', 'start_delay': 120},
] if a['token']]

LISTENING = {
    'song':     'adopt me scammer 2',
    'artist':   "safe 'n' sound",
    'album':    'bypassed audios 2',
    'track_id': '3eDqBONmO5jZqWRygjYnug',
}

_RPC_START_MS     = int(time.time() * 1000) - (1000 * 24 * 60 * 60 * 1000)
_SPOTIFY_START_MS = int(time.time() * 1000) - int(13 * 365.25 * 24 * 60 * 60 * 1000)
_SPOTIFY_PARTY_ID   = f"spotify:{uuid.uuid4().hex}"
_SPOTIFY_SESSION_ID = uuid.uuid4().hex

def build_activities():
    listening = {
        'type': 2,
        'name': 'Spotify',
        'details': LISTENING['song'],
        'state': LISTENING['artist'],
        'assets': {
            'large_image': 'spotify:ab67616d00001e02b4ffda0748b9182910e5656a',
            'large_text': LISTENING['album'],
        },
        'timestamps': {'start': _SPOTIFY_START_MS},
        'sync_id': LISTENING['track_id'],
        'party_id': _SPOTIFY_PARTY_ID,
        'flags': 48,
        'session_id': _SPOTIFY_SESSION_ID,
        'buttons': ['Listen on Spotify'],
        'metadata': {'button_urls': [f"https://open.spotify.com/track/{LISTENING['track_id']}"]},
    }
    playing = {
        'name': RICH_PRESENCE['name'], 'type': 0,
        'application_id': RICH_PRESENCE['applicationId'],
        'details': RICH_PRESENCE['details'], 'state': RICH_PRESENCE['state'],
        'timestamps': {'start': _RPC_START_MS},
        'buttons': [b['label'] for b in RICH_PRESENCE['buttons']],
        'metadata': {'button_urls': [b['url'] for b in RICH_PRESENCE['buttons']]},
    }
    return [playing, listening]

def build_activity():
    return build_activities()[1]

def parse_disboard_delay(text):
    if not text:
        return None
    ms = 0
    m = re.search(r'(\d+)\s*hour', text, re.I)
    if m: ms += int(m.group(1)) * 3600000
    m = re.search(r'(\d+)\s*min', text, re.I)
    if m: ms += int(m.group(1)) * 60000
    m = re.search(r'(\d+)\s*sec', text, re.I)
    if m: ms += int(m.group(1)) * 1000
    return ms if ms > 0 else None

def extract_msg_text(msg):
    parts = []
    if msg.get('content'): parts.append(msg['content'])
    for e in (msg.get('embeds') or []):
        if e.get('title'): parts.append(e['title'])
        if e.get('description'): parts.append(e['description'])
    return ' '.join(parts)

async def get_bump_cmd(tok, channel_id, guild_id):
    try:
        res = await api('GET', f'/guilds/{guild_id}/application-command-index', token=tok)
        cmd = next((c for c in (res.get('application_commands') or [])
                    if c.get('application_id') == DISBOARD_ID and c.get('name') == 'bump'), None)
        if cmd: return cmd
    except Exception: pass
    try:
        res = await api('GET', f'/channels/{channel_id}/application-commands/search?type=1&query=bump&limit=25', token=tok)
        cmd = next((c for c in (res.get('application_commands') or [])
                    if c.get('application_id') == DISBOARD_ID and c.get('name') == 'bump'), None)
        if cmd: return cmd
    except Exception: pass
    return {'id': '947088344167366698', 'version': '1051151064008769576',
            'application_id': DISBOARD_ID, 'name': 'bump', 'type': 1}

async def send_bump(tok, label, channel_id, guild_id, sid, cmd):
    try:
        nonce = str((int(time.time() * 1000) - 1420070400000) << 22)
        await api('POST', '/interactions', token=tok, base='https://discord.com/api/v9', body={
            'type': 2, 'application_id': DISBOARD_ID,
            'guild_id': guild_id, 'channel_id': channel_id,
            'session_id': sid, 'nonce': nonce,
            'data': {
                'version': cmd['version'], 'id': cmd['id'],
                'name': 'bump', 'type': 1, 'options': [], 'attachments': [],
                'application_command': {
                    'id': cmd['id'], 'application_id': DISBOARD_ID,
                    'version': cmd['version'], 'default_member_permissions': None,
                    'type': 1, 'name': 'bump',
                    'description': 'Bumps your server on DISBOARD',
                    'dm_permission': True, 'contexts': None, 'integration_types': [0]
                }
            }
        })
        print(f'[{label}] ✅ Bump sent!', flush=True)
        return True
    except Exception as e:
        print(f'[{label}] ❌ Bump failed: {e}', flush=True)
        return False

# ─── BUMPER (uses existing discord.py-self connection, no second WebSocket) ────

_disboard_futures = {}

async def keep_presence(c):
    while True:
        try:
            if c.ws:
                await c.ws.send_as_json({
                    'op': 3,
                    'd': {
                        'since': int(time.time() * 1000),
                        'status': 'online',
                        'afk': False,
                        'activities': build_activities()
                    }
                })
        except Exception:
            pass
        await asyncio.sleep(5)

async def bump_loop(acct, c):
    tok = acct['token']
    label = acct['label']
    channel_id = str(acct['channel_id'])
    start_delay = acct.get('start_delay', 0)

    await asyncio.sleep(5 + start_delay * 2)

    server_name = label
    guild_id = None
    try:
        ch = await api('GET', f'/channels/{channel_id}', token=tok)
        guild_id = ch.get('guild_id')
        ch_name = ch.get('name', channel_id)
        try:
            guild = await api('GET', f'/guilds/{guild_id}', token=tok)
            server_name = guild.get('name', label)
        except Exception:
            pass
        print(f'[{label}] 📋 Bumping #{ch_name} in {server_name}', flush=True)
    except Exception as e:
        print(f'[{label}] ❌ Could not fetch channel: {e}', flush=True)
        return

    cmd = await get_bump_cmd(tok, channel_id, guild_id)

    asyncio.create_task(quest_loop(tok, server_name))

    while True:
        sid = c.ws.session_id if c.ws else None
        await send_bump(tok, server_name, channel_id, guild_id, sid, cmd)

        fut = asyncio.get_event_loop().create_future()
        _disboard_futures[channel_id] = fut
        try:
            reply = await asyncio.wait_for(asyncio.shield(fut), timeout=20)
        except asyncio.TimeoutError:
            reply = None
        finally:
            _disboard_futures.pop(channel_id, None)

        delay_ms = 7200000
        if reply:
            parsed = parse_disboard_delay(reply)
            if parsed:
                delay_ms = parsed + 5000
                print(f'[{server_name}] ⏰ Next bump in {round(delay_ms / 60000)} min', flush=True)
            elif re.search(r'bump done|bumped', reply, re.I):
                print(f'[{server_name}] ⏰ Bump done! Next in 120 min', flush=True)
            else:
                print(f'[{server_name}] ⏰ Could not parse reply, defaulting 120 min', flush=True)
        else:
            print(f'[{server_name}] ⏰ No reply, defaulting 120 min', flush=True)

        await asyncio.sleep(delay_ms / 1000)

async def quest_loop(tok, server_name):
    await asyncio.sleep(10)
    while True:
        try:
            await run_auto_quest(tok, server_name)
        except Exception as e:
            print(f'[{server_name}] ❌ Quest loop error: {e}', flush=True)
        print(f'[{server_name}] ⏰ Next quest check in 4h', flush=True)
        await asyncio.sleep(4 * 3600)

# ─── CLONE PROGRESS ────────────────────────────────────────────────────────────

class CloneProgress:
    def __init__(self, channel, src_name, tgt_name, has_community, skip_set=None):
        skip_set = skip_set or set()
        self.channel = channel
        self.msg = None
        self.last_edit = 0
        self.header = f'🔁 Cloning: **{src_name}** → **{tgt_name}**'

        def s(key, label):
            skipped = key in skip_set
            return {'key': key, 'label': label, 'status': 'skip' if skipped else 'pending', 'detail': 'skipped' if skipped else ''}

        self.current_step = None
        self.steps = [{'key': 'cleanup', 'label': '🗑️  Cleanup', 'status': 'pending', 'detail': ''}]
        self.steps += [
            s('roles',      '👑  Roles'),
            s('categories', '📁  Categories'),
        ]
        if has_community:
            self.steps.append({'key': 'community', 'label': '🏘️  Community Mode', 'status': 'pending', 'detail': ''})
        self.steps += [
            s('channels',   '💬  Channels'),
            s('emojis',     '😀  Emojis'),
            s('stickers',   '🎭  Stickers'),
            s('soundboard', '🔊  Soundboard'),
            s('settings',   '🏠  Server Settings'),
            s('onboarding', '🚪  Onboarding'),
        ]

    def build(self):
        done = sum(1 for s in self.steps if s['status'] in ('done', 'warn'))
        pct = math.floor(done / len(self.steps) * 100)
        filled = math.floor(pct / 5)
        bar = '█' * filled + '░' * (20 - filled)
        lines = [self.header, f'`[{bar}] {pct}%`', '']
        for s in self.steps:
            icon = {'done': '✅', 'warn': '✅', 'active': '⏳', 'error': '⚠️', 'skip': '⏭️'}.get(s['status'], '⬜')
            lines.append(f'{icon}  {s["label"]}' + (f'  —  {s["detail"]}' if s['detail'] else ''))
        return '\n'.join(lines)

    async def send(self):
        if not self.channel: return
        try:
            self.msg = await self.channel.send(self.build())
        except Exception: pass

    async def set(self, key, status, detail=''):
        for s in self.steps:
            if s['key'] == key:
                s['status'] = status
                if detail: s['detail'] = detail
                if status == 'active':
                    self.current_step = key
                break
        await self._edit()

    async def _edit(self):
        if not self.msg: return
        now = time.time()
        wait = 1.5 - (now - self.last_edit)
        if wait > 0: await asyncio.sleep(wait)
        self.last_edit = time.time()
        try: await self.msg.edit(content=self.build())
        except Exception: pass

    async def finish(self, stats):
        if not self.msg: return
        try: await self.msg.edit(content=self.build() + f'\n\n{stats}\n🎉 **Done!**')
        except Exception: pass

# ─── SERVER CLONER ─────────────────────────────────────────────────────────────

CH_TYPE = {
    discord.ChannelType.text: 0,
    discord.ChannelType.voice: 2,
    discord.ChannelType.category: 4,
    discord.ChannelType.news: 5,
    discord.ChannelType.stage_voice: 13,
    discord.ChannelType.forum: 15,
}

class ServerCloner:
    def __init__(self, client, token):
        self.client = client
        self.token = token
        self.role_map = {}    # src_id → tgt_id (ints)
        self.cat_map = {}
        self.ch_map = {}
        self.stats = {k: 0 for k in ('roles', 'cats', 'channels', 'emojis', 'stickers', 'sounds', 'failed')}
        self.progress = None
        self.abort = set()

    def _stat(self, key): self.stats[key] += 1
    def _fail(self): self.stats['failed'] += 1

    async def _api(self, method, path, body=None, form=None):
        return await api(method, path, body=body, form=form, token=self.token)

    async def clone_server(self, src_id, tgt_id, prog_ch=None, skip=None):
        skip = skip or set()
        src = self.client.get_guild(int(src_id))
        tgt = self.client.get_guild(int(tgt_id))
        if not src: raise Exception('Source server not found!')
        if not tgt: raise Exception('Target server not found!')

        has_community = 'COMMUNITY' in (src.features or [])
        self.progress = CloneProgress(prog_ch, src.name, tgt.name, has_community, skip)
        await self.progress.send()

        await self._cleanup(tgt)
        if 'roles'      not in skip: await self._clone_roles(src, tgt)
        else:                         await self.progress.set('roles', 'skip', 'skipped')
        if 'categories' not in skip: await self._clone_categories(src, tgt)
        else:                         await self.progress.set('categories', 'skip', 'skipped')
        if has_community:            await self._enable_community(tgt, prog_ch)
        if 'channels'   not in skip: await self._clone_channels(src, tgt, prog_ch)
        else:                         await self.progress.set('channels', 'skip', 'skipped')
        if 'emojis'     not in skip: await self._clone_emojis(src, tgt)
        else:                         await self.progress.set('emojis', 'skip', 'skipped')
        if 'stickers'   not in skip: await self._clone_stickers(src, tgt)
        else:                         await self.progress.set('stickers', 'skip', 'skipped')
        if 'soundboard' not in skip: await self._clone_soundboard(src.id, tgt.id)
        else:                         await self.progress.set('soundboard', 'skip', 'skipped')
        if 'settings'   not in skip: await self._clone_settings(src, tgt)
        else:                         await self.progress.set('settings', 'skip', 'skipped')
        if 'onboarding' not in skip: await self._clone_onboarding(str(src.id), str(tgt.id), prog_ch)
        else:                         await self.progress.set('onboarding', 'skip', 'skipped')
        if has_community:            await self._clone_server_guide(str(src.id), str(tgt.id), prog_ch)

        s = self.stats
        total = s['roles'] + s['cats'] + s['channels'] + s['emojis'] + s['stickers'] + s['sounds']
        rate = round(total / max(total + s['failed'], 1) * 100)
        stats_line = (f"📊 {s['roles']} roles | {s['cats']} cats | {s['channels']} channels | "
                      f"{s['emojis']} emojis | {s['stickers']} stickers | {s['sounds']} sounds | "
                      f"❌ {s['failed']} failed | 📈 {rate}%")
        await self.progress.finish(stats_line)

    def _map_overwrites(self, channel, tgt_guild):
        result = []
        for target, ow in channel.overwrites.items():
            if isinstance(target, discord.Role):
                if target.id == channel.guild.id:
                    tgt_id = str(tgt_guild.id)
                else:
                    mapped = self.role_map.get(target.id)
                    if not mapped: continue
                    tgt_id = str(mapped)
                allow, deny = ow.pair()
                result.append({'id': tgt_id, 'type': 0, 'allow': str(allow.value), 'deny': str(deny.value)})
        return result

    async def _cleanup(self, guild):
        await self.progress.set('cleanup', 'active')
        for ch in list(guild.channels):
            try: await self._api('DELETE', f'/channels/{ch.id}'); await sleep_ms(300)
            except Exception: self._fail()
        for role in list(guild.roles):
            if role.name == '@everyone' or role.managed or not role.is_assignable(): continue
            try: await self._api('DELETE', f'/guilds/{guild.id}/roles/{role.id}'); await sleep_ms(300)
            except Exception: self._fail()
        for emoji in list(guild.emojis):
            try: await self._api('DELETE', f'/guilds/{guild.id}/emojis/{emoji.id}'); await sleep_ms(300)
            except Exception: pass
        try:
            stickers = await self._api('GET', f'/guilds/{guild.id}/stickers') or []
            for st in stickers:
                try: await self._api('DELETE', f'/guilds/{guild.id}/stickers/{st["id"]}'); await sleep_ms(300)
                except Exception: pass
        except Exception: pass
        try:
            res = await self._api('GET', f'/guilds/{guild.id}/soundboard-sounds')
            sounds = res.get('items', res) if isinstance(res, dict) else res or []
            for snd in sounds:
                if snd.get('guild_id'):
                    try: await self._api('DELETE', f'/guilds/{guild.id}/soundboard-sounds/{snd["sound_id"]}'); await sleep_ms(300)
                    except Exception: pass
        except Exception: pass
        await self.progress.set('cleanup', 'done')

    async def _enable_community(self, guild, prog_ch):
        await self.progress.set('community', 'active')
        # Always re-create rules/updates channels because _cleanup deleted them.
        # Even if the guild cache still shows COMMUNITY, the required channels are gone
        # and Discord will reject channel creation until they're restored.
        rules_id = updates_id = None
        try:
            r = await self._api('POST', f'/guilds/{guild.id}/channels', {'name': 'rules', 'type': 0})
            rules_id = r['id']; await sleep_ms(500)
            u = await self._api('POST', f'/guilds/{guild.id}/channels', {'name': 'updates', 'type': 0})
            updates_id = u['id']; await sleep_ms(500)
        except Exception as e:
            if 'COMMUNITY' in (guild.features or []):
                # Community already enabled and channels still exist somehow — carry on
                await self.progress.set('community', 'done', 'already enabled'); return
            await self.progress.set('community', 'error', str(e)[:40]); return
        try:
            await self._api('PATCH', f'/guilds/{guild.id}', {
                'verification_level': 2, 'explicit_content_filter': 2,
                'features': ['COMMUNITY'],
                'rules_channel_id': rules_id, 'public_updates_channel_id': updates_id
            })
            await guild.fetch()
            await self.progress.set('community', 'done')
        except Exception as e:
            await self.progress.set('community', 'error', str(e)[:40])

    async def _clone_roles(self, src, tgt):
        await self.progress.set('roles', 'active')
        roles = sorted([r for r in src.roles if r.name != '@everyone' and not r.managed], key=lambda r: r.position)
        positions = []
        for role in roles:
            try:
                new = await self._api('POST', f'/guilds/{tgt.id}/roles', {
                    'name': role.name, 'color': role.colour.value,
                    'permissions': str(role.permissions.value),
                    'hoist': role.hoist, 'mentionable': role.mentionable
                })
                self.role_map[role.id] = int(new['id'])
                positions.append({'id': new['id'], 'position': role.position})
                self._stat('roles')
                await sleep_ms(250)
            except Exception as e:
                log_err(f'[Clone] Role "{role.name}": {e}')
                self._fail()
        if positions:
            try: await self._api('PATCH', f'/guilds/{tgt.id}/roles', positions)
            except Exception: pass
        await self.progress.set('roles', 'done', f'{self.stats["roles"]} created')

    async def _clone_categories(self, src, tgt):
        await self.progress.set('categories', 'active')
        cats = sorted([c for c in src.channels if c.type == discord.ChannelType.category], key=lambda c: c.position)
        for cat in cats:
            try:
                body = {'name': cat.name, 'type': 4, 'position': cat.position,
                        'permission_overwrites': self._map_overwrites(cat, tgt)}
                new = await self._api('POST', f'/guilds/{tgt.id}/channels', body)
                self.cat_map[cat.id] = int(new['id'])
                self._stat('cats')
                await sleep_ms(300)
            except Exception as e:
                log_err(f'[Clone] Category "{cat.name}": {e}')
                self._fail()
        await self.progress.set('categories', 'done', f'{self.stats["cats"]} created')

    async def _clone_channels(self, src, tgt, prog_ch):
        await self.progress.set('channels', 'active')
        allowed_types = {discord.ChannelType.text, discord.ChannelType.voice,
                         discord.ChannelType.news, discord.ChannelType.stage_voice, discord.ChannelType.forum}
        community_only = {discord.ChannelType.news, discord.ChannelType.stage_voice, discord.ChannelType.forum}
        # Re-fetch community status from API so we get the live value after _enable_community ran
        try:
            fresh = await self._api('GET', f'/guilds/{tgt.id}')
            tgt_is_community = 'COMMUNITY' in (fresh.get('features') or [])
        except Exception:
            tgt_is_community = 'COMMUNITY' in (tgt.features or [])

        channels = sorted([c for c in src.channels if c.type in allowed_types], key=lambda c: c.position)
        total = len(channels)
        last_upd = 0
        for ch in channels:
            if 'channels' in self.abort: break
            if ch.type in community_only and not tgt_is_community: continue
            try:
                ch_type = ch.type
                if ch_type == discord.ChannelType.news and not tgt_is_community:
                    ch_type = discord.ChannelType.text
                body = {
                    'name': ch.name, 'type': CH_TYPE.get(ch_type, 0),
                    'position': ch.position,
                    'permission_overwrites': self._map_overwrites(ch, tgt),
                }
                parent = self.cat_map.get(ch.category_id)
                if parent: body['parent_id'] = str(parent)
                if ch_type in (discord.ChannelType.text, discord.ChannelType.news):
                    body.update({'topic': ch.topic or '', 'nsfw': ch.nsfw, 'rate_limit_per_user': ch.slowmode_delay})
                elif ch_type in (discord.ChannelType.voice, discord.ChannelType.stage_voice):
                    body.update({'bitrate': min(getattr(ch, 'bitrate', 64000), 96000), 'user_limit': getattr(ch, 'user_limit', 0)})
                elif ch_type == discord.ChannelType.forum:
                    body.update({'topic': ch.topic or '', 'nsfw': ch.nsfw, 'rate_limit_per_user': ch.slowmode_delay})
                new = await self._api('POST', f'/guilds/{tgt.id}/channels', body)
                self.ch_map[ch.id] = int(new['id'])
                self._stat('channels')
                if self.stats['channels'] - last_upd >= 3:
                    last_upd = self.stats['channels']
                    await self.progress.set('channels', 'active', f'{self.stats["channels"]}/{total}')
                await sleep_ms(300)
            except Exception as e:
                log_err(f'[Clone] Channel "{ch.name}": {e}')
                self._fail()
        await self.progress.set('channels', 'done', f'{self.stats["channels"]} created')

    async def _clone_emojis(self, src, tgt):
        await self.progress.set('emojis', 'active')
        tier_slots = {0: 50, 1: 100, 2: 150, 3: 250}
        max_slots = tier_slots.get(tgt.premium_tier, 50)
        static_left = max_slots - sum(1 for e in tgt.emojis if not e.animated)
        anim_left = max_slots - sum(1 for e in tgt.emojis if e.animated)
        skipped = 0
        last_upd = 0
        all_emojis = list(src.emojis)
        for emoji in all_emojis:
            if 'emojis' in self.abort: skipped += 1; continue
            if emoji.animated and anim_left <= 0: skipped += 1; continue
            if not emoji.animated and static_left <= 0: skipped += 1; continue
            try:
                data, mime = await download_bytes(str(emoji.url))
                b64 = f'data:{mime};base64,{base64.b64encode(data).decode()}'
                await self._api('POST', f'/guilds/{tgt.id}/emojis', {'name': emoji.name, 'image': b64})
                self._stat('emojis')
                if emoji.animated: anim_left -= 1
                else: static_left -= 1
                if self.stats['emojis'] - last_upd >= 5:
                    last_upd = self.stats['emojis']
                    await self.progress.set('emojis', 'active', f'{self.stats["emojis"]}/{len(all_emojis)}')
                await sleep_ms(600)
            except Exception as e:
                msg = str(e)
                if '30008' in msg or 'maximum number of emojis' in msg.lower():
                    if emoji.animated: anim_left = 0
                    else: static_left = 0
                    skipped += 1
                else:
                    log_warn(f'[Clone] Emoji "{emoji.name}" failed, skipping: {str(e)[:80]}')
                    skipped += 1
                    self._fail()
        detail = f'{self.stats["emojis"]} copied, {skipped} skipped' if skipped else f'{self.stats["emojis"]} created'
        await self.progress.set('emojis', 'done', detail)

    async def _clone_stickers(self, src, tgt):
        await self.progress.set('stickers', 'active')
        try:
            stickers = await self._api('GET', f'/guilds/{src.id}/stickers') or []
        except Exception as e:
            await self.progress.set('stickers', 'error', str(e)[:40]); return
        if not stickers:
            await self.progress.set('stickers', 'done', 'none to clone'); return
        last_upd = 0
        for st in stickers:
            if 'stickers' in self.abort: break
            try:
                ext = 'json' if st['format_type'] == 3 else 'apng' if st['format_type'] == 2 else 'png'
                url = f'https://cdn.discordapp.com/stickers/{st["id"]}.{ext}'
                data, mime = await download_bytes(url, self.token)
                form = aiohttp.FormData()
                form.add_field('name', st['name'])
                form.add_field('description', st.get('description') or st['name'])
                form.add_field('tags', st.get('tags') or st['name'])
                form.add_field('file', data, filename=f'sticker.{ext}', content_type=mime)
                await self._api('POST', f'/guilds/{tgt.id}/stickers', form=form)
                self._stat('stickers')
                if self.stats['stickers'] - last_upd >= 3:
                    last_upd = self.stats['stickers']
                    await self.progress.set('stickers', 'active', f'{self.stats["stickers"]}/{len(stickers)}')
                await sleep_ms(1500)
            except Exception: self._fail()
        await self.progress.set('stickers', 'done', f'{self.stats["stickers"]} created')

    async def _clone_soundboard(self, src_id, tgt_id):
        await self.progress.set('soundboard', 'active')
        try:
            res = await self._api('GET', f'/guilds/{src_id}/soundboard-sounds')
            sounds = [s for s in (res.get('items', res) if isinstance(res, dict) else res or []) if s.get('guild_id')]
        except Exception as e:
            await self.progress.set('soundboard', 'error', str(e)[:40]); return
        if not sounds:
            await self.progress.set('soundboard', 'done', 'none to clone'); return
        last_upd = 0
        for snd in sounds:
            if 'soundboard' in self.abort: break
            try:
                url = f'https://cdn.discordapp.com/soundboard-sounds/{snd["sound_id"]}'
                data, mime = await download_bytes(url, self.token)
                b64 = f'data:{mime};base64,{base64.b64encode(data).decode()}'
                body = {'name': snd['name'], 'sound': b64, 'volume': snd.get('volume', 1)}
                if snd.get('emoji_id'): body['emoji_id'] = snd['emoji_id']
                if snd.get('emoji_name'): body['emoji_name'] = snd['emoji_name']
                await self._api('POST', f'/guilds/{tgt_id}/soundboard-sounds', body)
                self._stat('sounds')
                if self.stats['sounds'] - last_upd >= 3:
                    last_upd = self.stats['sounds']
                    await self.progress.set('soundboard', 'active', f'{self.stats["sounds"]}/{len(sounds)}')
                await sleep_ms(1500)
            except Exception: self._fail()
        await self.progress.set('soundboard', 'done', f'{self.stats["sounds"]} sounds')

    async def _clone_settings(self, src, tgt):
        await self.progress.set('settings', 'active')
        vl = {'none': 0, 'low': 1, 'medium': 2, 'high': 3, 'extreme': 4}
        patch = {
            'name': src.name,
            'verification_level': vl.get(str(src.verification_level).lower(), 0),
            'default_message_notifications': src.default_notifications.value,
            'explicit_content_filter': src.explicit_content_filter.value,
        }
        if src.afk_timeout: patch['afk_timeout'] = src.afk_timeout
        if src.preferred_locale: patch['preferred_locale'] = str(src.preferred_locale)
        if src.description: patch['description'] = src.description
        if src.icon:
            try: patch['icon'] = await download_b64(str(src.icon.replace(size=1024)))
            except Exception: pass
        if src.banner:
            try: patch['banner'] = await download_b64(str(src.banner.replace(size=1024)))
            except Exception: pass
        if src.splash:
            try: patch['splash'] = await download_b64(str(src.splash.replace(size=1024)))
            except Exception: pass
        try:
            await self._api('PATCH', f'/guilds/{tgt.id}', patch)
            await self.progress.set('settings', 'done', 'name + icon applied')
        except Exception as e:
            try:
                await self._api('PATCH', f'/guilds/{tgt.id}', {'name': src.name})
                await self.progress.set('settings', 'warn', str(e)[:50])
            except Exception as e2:
                await self.progress.set('settings', 'error', str(e2)[:50])

    async def _clone_onboarding(self, src_id, tgt_id, prog_ch):
        if self.progress is None:
            src_g = self.client.get_guild(int(src_id))
            tgt_g = self.client.get_guild(int(tgt_id))
            src_name = src_g.name if src_g else src_id
            tgt_name = tgt_g.name if tgt_g else tgt_id
            self.progress = CloneProgress(prog_ch, src_name, tgt_name, False, skip_set={
                'cleanup','roles','categories','channels','emojis','stickers','soundboard','settings','community'
            })
            await self.progress.send()
        await self.progress.set('onboarding', 'active')
        tgt = self.client.get_guild(int(tgt_id))
        if tgt and 'COMMUNITY' not in (tgt.features or []):
            log_warn(f'[Onboarding] Target server is not Community mode — onboarding may fail.')
            if prog_ch: await prog_ch.send('⚠️ Target server is not in **Community mode** — onboarding will be saved as disabled.')

        # Auto-map by name if no mappings
        if not self.ch_map or not self.role_map:
            src = self.client.get_guild(int(src_id))
            if src and tgt:
                if not self.ch_map:
                    for ch in src.channels:
                        match = discord.utils.get(tgt.channels, name=ch.name, type=ch.type)
                        if match: self.ch_map[ch.id] = match.id
                    log_inf(f'[Onboarding] Auto-mapped {len(self.ch_map)} channels')
                if not self.role_map:
                    for role in src.roles:
                        match = discord.utils.get(tgt.roles, name=role.name)
                        if match: self.role_map[role.id] = match.id
                    log_inf(f'[Onboarding] Auto-mapped {len(self.role_map)} roles')

        try:
            ob = await self._api('GET', f'/guilds/{src_id}/onboarding')
        except Exception as e:
            await self.progress.set('onboarding', 'error', str(e)[:40]); return

        if not ob or (not ob.get('prompts') and not ob.get('new_member_actions')):
            await self.progress.set('onboarding', 'done', 'none to clone')
            if prog_ch: await prog_ch.send('ℹ️ Source has no onboarding to copy.')
            return

        # Clone missing roles referenced in prompts
        ref_roles = set()
        for p in (ob.get('prompts') or []):
            for o in (p.get('options') or []):
                for rid in (o.get('role_ids') or []): ref_roles.add(int(rid))
        src_guild = self.client.get_guild(int(src_id))
        missing_roles = [r for r in (src_guild.roles if src_guild else [])
                         if r.id in ref_roles and r.id not in self.role_map and r.name != '@everyone']
        if missing_roles:
            if prog_ch: await prog_ch.send(f'🎭 Creating **{len(missing_roles)}** role(s) for onboarding...')
            for role in missing_roles:
                try:
                    new = await self._api('POST', f'/guilds/{tgt_id}/roles', {
                        'name': role.name, 'color': role.colour.value,
                        'permissions': str(role.permissions.value),
                        'hoist': role.hoist, 'mentionable': role.mentionable
                    })
                    self.role_map[role.id] = int(new['id']); await sleep_ms(300)
                except Exception: pass

        def map_chs(ids): return [str(self.ch_map[int(i)]) for i in (ids or []) if int(i) in self.ch_map]
        def map_roles(ids): return [str(self.role_map[int(i)]) for i in (ids or []) if int(i) in self.role_map]

        def map_emoji(e):
            if not e: return None
            # Only include emoji if it has a valid name
            name = e.get('name') or ''
            if not name: return None
            if e.get('id'):
                return {'id': str(e['id']), 'name': name, 'animated': bool(e.get('animated'))}
            return {'name': name}

        DISCORD_EPOCH = 1420070400000
        _snow_counter = [0]
        def new_snowflake():
            ms = int(time.time() * 1000) - DISCORD_EPOCH
            _snow_counter[0] += 1
            return str((ms << 22) | (_snow_counter[0] & 0xFFFFF))

        prompts = []
        for p in (ob.get('prompts') or []):
            p_type = int(p.get('type', 0))
            if p_type not in (0, 1): p_type = 0  # clamp: 0=multiple choice, 1=dropdown
            opts = []
            for o in (p.get('options') or []):
                opt = {
                    'id': new_snowflake(),
                    'title': str(o.get('title') or '')[:50],
                    'description': o.get('description') or None,
                    'role_ids': map_roles(o.get('role_ids')),
                    'channel_ids': map_chs(o.get('channel_ids')),
                }
                em = map_emoji(o.get('emoji'))
                if em: opt['emoji'] = em
                opts.append(opt)
            prompts.append({
                'id': new_snowflake(),
                'title': str(p.get('title') or '')[:100],
                'type': p_type,
                'single_select': bool(p.get('single_select')),
                'required': bool(p.get('required')),
                'in_onboarding': bool(p.get('in_onboarding')),
                'options': opts,
            })

        # Pad default_channel_ids to 7+
        DISCORD_MIN = 7
        default_chs = map_chs(ob.get('default_channel_ids'))
        if len(default_chs) < DISCORD_MIN and tgt:
            existing = set(default_chs)
            for ch in tgt.text_channels:
                if len(default_chs) >= DISCORD_MIN: break
                if str(ch.id) not in existing: default_chs.append(str(ch.id))

        # mode: 0=default, 1=advanced — clamp to valid range
        mode = int(ob.get('mode') or 0)
        if mode not in (0, 1): mode = 0

        payload = {'prompts': prompts, 'default_channel_ids': default_chs,
                   'enabled': True, 'mode': mode}

        guide_actions = [(a, self.ch_map.get(int(a.get('channel_id', 0)))) for a in (ob.get('new_member_actions') or [])]
        mapped_actions = []
        for action, tgt_ch_id in guide_actions:
            if not tgt_ch_id: continue
            a = {'channel_id': str(tgt_ch_id), 'action_type': action['action_type'],
                 'title': str(action.get('title') or ''), 'description': str(action.get('description') or '')}
            em = map_emoji(action.get('emoji'))
            if em: a['emoji'] = em
            if action.get('icon'): a['icon'] = action['icon']
            mapped_actions.append(a)
        if mapped_actions: payload['new_member_actions'] = mapped_actions

        enabled = True
        try:
            await self._api('PUT', f'/guilds/{tgt_id}/onboarding', payload)
        except Exception as e:
            err = str(e)
            log_warn(f'[Onboarding] PUT failed (full): {err}')
            if prog_ch:
                await prog_ch.send(f'⚠️ Onboarding attempt 1 failed:\n```\n{err[:800]}\n```\nRetrying with enabled=False...')
            # Retry with enabled=False and mode=0 (basic requirements)
            enabled = False
            payload_disabled = {**payload, 'enabled': False, 'mode': 0}
            try:
                await self._api('PUT', f'/guilds/{tgt_id}/onboarding', payload_disabled)
            except Exception as e2:
                err2 = str(e2)
                log_warn(f'[Onboarding] PUT disabled also failed: {err2}')
                if prog_ch:
                    await prog_ch.send(f'❌ Onboarding attempt 2 also failed:\n```\n{err2[:800]}\n```')
                await self.progress.set('onboarding', 'error', err2[:60]); return

        await self.progress.set('onboarding', 'done', f'{len(prompts)} prompts')
        if prog_ch:
            note = '✅ Onboarding **enabled**.' if enabled else '⚠️ Saved as **disabled** — enable in Server Settings when ready.'
            await prog_ch.send(f'📋 **Onboarding copied** — {len(prompts)} prompt(s), {len(mapped_actions)} guide action(s).\n{note}')

    async def _clone_server_guide(self, src_id, tgt_id, prog_ch):
        try:
            hs = await api('GET', f'/guilds/{src_id}/home-settings')
        except Exception: return
        if not hs or (not hs.get('new_member_actions') and not hs.get('resource_channels') and not hs.get('welcome_message')):
            return
        patch = {}
        if hs.get('welcome_message'):
            patch['welcome_message'] = {'author_ids': hs['welcome_message'].get('author_ids', []),
                                         'message': hs['welcome_message'].get('message', '')}
        if hs.get('new_member_actions'):
            actions = []
            for a in hs['new_member_actions']:
                tgt_ch = self.ch_map.get(int(a.get('channel_id', 0)))
                if not tgt_ch: continue
                item = {'channel_id': str(tgt_ch), 'action_type': a['action_type'],
                        'title': a.get('title', ''), 'description': a.get('description', '')}
                if a.get('emoji'):
                    item['emoji'] = {'id': a['emoji']['id'], 'name': a['emoji']['name']} if a['emoji'].get('id') else {'name': a['emoji']['name']}
                if a.get('icon'): item['icon'] = a['icon']
                actions.append(item)
            if actions: patch['new_member_actions'] = actions
        if hs.get('resource_channels'):
            patch['resource_channels'] = [str(self.ch_map[int(i)]) for i in hs['resource_channels'] if int(i) in self.ch_map]
        if not patch: return
        try:
            await self._api('PATCH', f'/guilds/{tgt_id}/home-settings', patch)
            if prog_ch:
                await prog_ch.send(f'🏠 **Server Guide copied** — {len(patch.get("new_member_actions", []))} action(s), {len(patch.get("resource_channels", []))} resource channel(s).')
        except Exception as e:
            if prog_ch: await prog_ch.send(f'❌ Server Guide copy failed: {e}')

# ─── DISCORD CLIENT ────────────────────────────────────────────────────────────

bot_msg_ids = set()

def track(msg):
    bot_msg_ids.add(msg.id)
    if len(bot_msg_ids) > 50:
        bot_msg_ids.discard(next(iter(bot_msg_ids)))

pending_ops = {}
active_cloners = {}

_build_fetched = False

def make_client(token):
    c = discord.Client()
    my_accounts = [a for a in BUMP_ACCOUNTS if a['token'] == token]

    @c.event
    async def on_ready():
        global _build_fetched
        log_ok(f'[Bot] Ready as {c.user}')
        if not _build_fetched:
            _build_fetched = True
            await fetch_build_number()
        asyncio.create_task(keep_presence(c))
        for acct in my_accounts:
            asyncio.create_task(bump_loop(acct, c))

    @c.event
    async def on_message(message):
        if message.author.id == c.user.id and message.id in bot_msg_ids:
            return

        # Route Disboard replies to the bump listener before bot-filter
        author_id = str(message.author.id)
        app_id = str(getattr(message, 'application_id', '') or '')
        if author_id == DISBOARD_ID or app_id == DISBOARD_ID:
            ch_id = str(message.channel.id)
            fut = _disboard_futures.get(ch_id)
            if fut and not fut.done():
                parts = []
                if message.content:
                    parts.append(message.content)
                for e in message.embeds:
                    if e.title: parts.append(e.title)
                    if e.description: parts.append(e.description)
                fut.set_result(' '.join(parts))
            return

        if message.author.bot and message.author.id != c.user.id:
            return

        uid = str(message.author.id)
        is_allowed = uid in ALLOWED_USER_IDS

        if message.content.startswith('!'):
            log_inf(f'[CMD] "{message.content[:60]}" from {uid} | allowed: {is_allowed}')
        if not is_allowed:
            return

        content = message.content

        # !skip during active clone
        if content.lower().startswith('!skip') and uid in active_cloners:
            cloner = active_cloners[uid]
            words = content[5:].strip().lower().split()
            valid = {'roles','categories','channels','emojis','stickers','soundboard','settings','onboarding'}
            # no args → skip whatever step is currently active
            if not words:
                cur = cloner.progress.current_step if cloner.progress else None
                if cur and cur in valid:
                    cloner.abort.add(cur)
                    m = await message.channel.send(f'⏩ Skipping current step: ⏭️ **{cur}**')
                    track(m)
                else:
                    m = await message.channel.send('⚠️ No active step to skip right now.')
                    track(m)
                return
            stopped = [f'⏭️ **{w}**' for w in words if w in valid and not cloner.abort.add(w)]
            if stopped:
                m = await message.channel.send(f'⏩ Stopping: {", ".join(stopped)}')
                track(m)
            else:
                m = await message.channel.send('❌ Valid steps: `roles` `categories` `channels` `emojis` `stickers` `soundboard` `settings` `onboarding`')
                track(m)
            return

        # Pending confirmation
        if uid in pending_ops:
            op = pending_ops[uid]

            if op['step'] == 'confirmProceed':
                if content.lower().startswith('!skip'):
                    words = content[5:].strip().lower().split()
                    valid = {'roles','categories','channels','emojis','stickers','soundboard','settings','onboarding'}
                    toggled = []
                    for w in words:
                        if w not in valid: continue
                        if w in op['skip']:
                            op['skip'].discard(w); toggled.append(f'✅ **{w}** re-enabled')
                        else:
                            op['skip'].add(w); toggled.append(f'⏭️ **{w}** skipped')
                    skip_list = ', '.join(op['skip']) if op['skip'] else 'none'
                    m = await message.channel.send('\n'.join(toggled) + f'\n\nSkipping: **{skip_list}**\nType **y** to start or **n** to cancel.')
                    track(m); return

                if content.lower().strip() not in ('y', 'yes', 'n', 'no'): return
                if content.lower().strip() in ('y', 'yes'):
                    op_copy = pending_ops.pop(uid)
                    skip_str = f' (skipping: {", ".join(op_copy["skip"])})' if op_copy['skip'] else ''
                    m = await message.channel.send(f'🚀 Starting clone{skip_str}...'); track(m)
                    try:
                        cloner = ServerCloner(c, token)
                        active_cloners[uid] = cloner
                        await cloner.clone_server(op_copy['src'], op_copy['tgt'], message.channel, op_copy['skip'])
                    except Exception as e:
                        m = await message.channel.send(f'❌ {e}'); track(m)
                    finally:
                        active_cloners.pop(uid, None)
                else:
                    pending_ops.pop(uid, None)
                    m = await message.channel.send('❌ Cancelled.'); track(m)
                return

        # !clone onboarding <src> <tgt>
        if content.lower().startswith('!clone onboarding'):
            pending_ops.pop(uid, None)
            args = content[17:].strip().split()
            if len(args) < 2:
                m = await message.channel.send('❌ Usage: `!clone onboarding <source ID> <target ID>`'); track(m); return
            src_g = c.get_guild(int(args[0]))
            tgt_g = c.get_guild(int(args[1]))
            if not src_g: m = await message.channel.send('❌ Source server not found.'); track(m); return
            if not tgt_g: m = await message.channel.send('❌ Target server not found.'); track(m); return
            m = await message.channel.send(f'📋 Copying onboarding **{src_g.name}** → **{tgt_g.name}**...'); track(m)
            try:
                cloner = ServerCloner(c, token)
                await cloner._clone_onboarding(str(src_g.id), str(tgt_g.id), message.channel)
            except Exception as e:
                m = await message.channel.send(f'❌ {e}'); track(m)
            return

        # !clone <src> <tgt>
        if content.startswith('!clone'):
            args = content[6:].strip().split()
            if len(args) < 2:
                m = await message.channel.send('❌ Usage: `!clone <source ID> <your server ID>`'); track(m); return
            src_g = c.get_guild(int(args[0]))
            tgt_g = c.get_guild(int(args[1]))
            if not src_g: m = await message.channel.send('❌ Source server not found.'); track(m); return
            if not tgt_g: m = await message.channel.send('❌ Target server not found.'); track(m); return
            pending_ops[uid] = {'step': 'confirmProceed', 'src': str(src_g.id), 'tgt': str(tgt_g.id), 'skip': set()}
            m = await message.channel.send(
                f'📋 **Server Clone Confirmation**\n'
                f'📥 FROM: **{src_g.name}**\n📤 INTO: **{tgt_g.name}**\n\n'
                f'⚠️ All existing channels/roles/emojis in **{tgt_g.name}** will be deleted!\n\n'
                f'💡 `!skip emojis`, `!skip soundboard`, etc. to skip steps.\n'
                f'Skippable: `roles` `categories` `channels` `emojis` `stickers` `soundboard` `settings` `onboarding`\n\n'
                f'Proceed? (y/n)'
            ); track(m)
            return

        # !servermemberinfo <guild_id>
        if content.startswith('!servermemberinfo'):
            args = content[17:].strip().split()
            if not args:
                m = await message.channel.send('❌ Usage: `!servermemberinfo <server ID>`'); track(m); return
            guild = c.get_guild(int(args[0]))
            if not guild: m = await message.channel.send('❌ Server not found.'); track(m); return
            m = await message.channel.send(f'🔍 Fetching members for **{guild.name}**...'); track(m)
            try:
                members = [mem async for mem in guild.fetch_members(limit=None) if not mem.bot]
            except Exception as e:
                m = await message.channel.send(f'❌ {e}'); track(m); return
            file_content = '\n'.join(f'{mem.id},' for mem in members)
            f = discord.File(fp=io.BytesIO(file_content.encode()), filename=f'members_{guild.name}.txt')
            m = await message.channel.send(content=f'👥 **{guild.name}** — **{len(members)}** members', file=f); track(m)
            return

    return c

# ─── ENTRY POINT ───────────────────────────────────────────────────────────────

async def safe_start(client, tok, label):
    try:
        await client.start(tok)
    except discord.LoginFailure:
        log_err(f'[{label}] ❌ Invalid or expired token — skipping this account.')
    except Exception as e:
        log_err(f'[{label}] ❌ Connection lost: {e} — skipping this account.')

async def main():
    tokens = [(TOKEN, 'Account 1'), (TOKEN_2, 'Account 2'), (TOKEN_3, 'Account 3')]
    clients = []
    for tok, label in tokens:
        if tok:
            clients.append((make_client(tok), tok, label))

    if not clients:
        log_err('No tokens configured. Set DISCORD_TOKEN (and optionally DISCORD_TOKEN_2 / DISCORD_TOKEN_3).')
        return

    print(f'🚀 Starting {len(clients)} account(s)...', flush=True)
    await asyncio.gather(*[safe_start(c, tok, label) for c, tok, label in clients])

asyncio.run(main())
