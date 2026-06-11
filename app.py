import streamlit as st
import json, re
from datetime import datetime
from collections import Counter
import urllib.request, urllib.parse, urllib.error

YOUTUBE_API_KEY = st.secrets["YOUTUBE_API_KEY"]

st.set_page_config(page_title="YouTube 動画分析ツール", page_icon="🎬", layout="wide")

st.markdown("""
<style>
.tag-good { background:#dcfce7; color:#166534; padding:4px 12px; border-radius:6px; font-size:12px; font-weight:600; margin:3px; display:inline-block; }
.tag-bad  { background:#fee2e2; color:#991b1b; padding:4px 12px; border-radius:6px; font-size:12px; font-weight:600; margin:3px; display:inline-block; }
.impr-box { background:#fffbeb; border-left:3px solid #f59e0b; border-radius:0 8px 8px 0; padding:10px 14px; margin:6px 0; font-size:13px; line-height:1.7; color:#1a1a1a !important; }
.critical-box { background:#fff1f2; border-left:3px solid #f43f5e; border-radius:0 8px 8px 0; padding:10px 14px; margin:6px 0; font-size:13px; line-height:1.7; color:#1a1a1a !important; }
.buzz-banner { background:linear-gradient(90deg,#fef3c7,#fde68a); border:1px solid #f59e0b; border-radius:10px; padding:12px 18px; margin:8px 0; font-size:14px; font-weight:700; color:#92400e; }
.win-box { background:#ecfdf5; border:1px solid #10b981; border-radius:10px; padding:10px 16px; margin:6px 0; font-size:13px; color:#065f46; }
</style>
""", unsafe_allow_html=True)


# ════════════════════════════════════════════════════════
# YouTube API（クォータカウンター付き）
# ════════════════════════════════════════════════════════

def yt_api(endpoint, params):
    cost = 100 if endpoint == 'search' else 1
    try:
        st.session_state['quota_used'] = st.session_state.get('quota_used', 0) + cost
    except Exception:
        pass
    params['key'] = YOUTUBE_API_KEY
    url = "https://www.googleapis.com/youtube/v3/" + endpoint + "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read())

def extract_video_id(url):
    m = re.search(r'(?:v=|youtu\.be/|shorts/|live/|embed/)([A-Za-z0-9_-]{11})', url)
    return m.group(1) if m else None

def parse_duration(s):
    m = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', s)
    if not m: return 0
    return int(m.group(1) or 0)*3600 + int(m.group(2) or 0)*60 + int(m.group(3) or 0)

def _median(lst):
    s = sorted(lst)
    n = len(s)
    if n == 0: return 0
    return s[n//2] if n % 2 else (s[n//2 - 1] + s[n//2]) / 2


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_video(video_id):
    from datetime import timezone
    d = yt_api('videos', {'part': 'snippet,statistics,contentDetails', 'id': video_id})
    if not d.get('items'):
        return None
    v = d['items'][0]
    sn = v['snippet']
    st_ = v.get('statistics', {})
    channel_id = sn['channelId']
    ch = yt_api('channels', {'part': 'statistics', 'id': channel_id})
    ch_stats = ch['items'][0].get('statistics', {}) if ch.get('items') else {}
    duration = parse_duration(v['contentDetails']['duration'])
    published_at = sn['publishedAt']
    pub_date = datetime.strptime(published_at, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    days_since = max(1, (now - pub_date).days)
    pub_jst_hour = (pub_date.hour + 9) % 24
    pub_weekday = pub_date.weekday()  # 0=Mon
    title_desc = (sn['title'] + sn.get('description', '')).lower()
    # Shortsは現在3分まで。60秒以下 or #shortsタグ付き3分以下をShorts扱い
    is_short = duration <= 60 or (duration <= 180 and '#shorts' in title_desc)
    return {
        'id': video_id,
        'channel_id': channel_id,
        'title': sn['title'],
        'description': sn.get('description', ''),
        'tags': sn.get('tags', []),
        'published': published_at[:10],
        'days_since': days_since,
        'pub_jst_hour': pub_jst_hour,
        'pub_weekday': pub_weekday,
        'channel_title': sn['channelTitle'],
        'views': int(st_.get('viewCount', 0)),
        'likes': int(st_.get('likeCount', 0)),
        'comments': int(st_.get('commentCount', 0)),
        'duration': duration,
        'is_short': is_short,
        'has_custom_thumb': 'maxres' in sn.get('thumbnails', {}),
        'thumbnail_url': sn.get('thumbnails', {}).get('maxres', sn.get('thumbnails', {}).get('high', {})).get('url', ''),
        'subscribers': int(ch_stats.get('subscriberCount', 0)),
        'total_channel_videos': int(ch_stats.get('videoCount', 0)),
    }


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_channel_benchmark(channel_id, exclude_id, max_results=25):
    """チャンネルの直近動画からベンチマーク統計を計算（外れ値に強い中央値ベース）"""
    from datetime import timezone
    try:
        search_data = yt_api('search', {
            'part': 'id', 'channelId': channel_id,
            'order': 'date', 'type': 'video', 'maxResults': max_results
        })
        if not search_data.get('items'):
            return None
        video_ids = [item['id']['videoId'] for item in search_data['items']
                     if item['id'].get('videoId') != exclude_id]
        if not video_ids:
            return None
        vdata = yt_api('videos', {
            'part': 'snippet,statistics,contentDetails',
            'id': ','.join(video_ids[:20])
        })
        now = datetime.now(timezone.utc)
        views_list, vpd_list, like_rates, durations = [], [], [], []
        for item in vdata.get('items', []):
            st_ = item.get('statistics', {})
            views = int(st_.get('viewCount', 0))
            likes = int(st_.get('likeCount', 0))
            pub = item['snippet']['publishedAt']
            pub_date = datetime.strptime(pub, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
            days = max(1, min(365, (now - pub_date).days))
            dur = parse_duration(item['contentDetails']['duration'])
            if views > 0:
                views_list.append(views)
                vpd_list.append(views / days)
                if likes > 0:
                    like_rates.append(likes / views * 100)
            if dur > 60:
                durations.append(dur)
        if not views_list:
            return None
        return {
            'avg_views': _median(views_list),
            'avg_vpd': _median(vpd_list),
            'avg_like_rate': _median(like_rates) if like_rates else 0,
            'avg_duration': _median(durations) if durations else 0,
            'max_views': max(views_list),
            'mean_views': sum(views_list) / len(views_list),
            'sample': len(views_list),
        }
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_comments(video_id, max_results=50):
    """上位コメントを取得（コメント無効の動画はNone）"""
    try:
        d = yt_api('commentThreads', {
            'part': 'snippet', 'videoId': video_id,
            'maxResults': max_results, 'order': 'relevance',
            'textFormat': 'plainText'
        })
        out = []
        for item in d.get('items', []):
            c = item['snippet']['topLevelComment']['snippet']
            out.append({
                'text': c.get('textDisplay', ''),
                'likes': int(c.get('likeCount', 0)),
                'author': c.get('authorDisplayName', ''),
            })
        return out
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def resolve_channel(s):
    """チャンネルURL・@ハンドル・動画URLからチャンネルIDと基本情報を取得"""
    try:
        m = re.search(r'channel/(UC[\w-]{22})', s)
        if m:
            cid = m.group(1)
            d = yt_api('channels', {'part': 'snippet,statistics', 'id': cid})
        else:
            vid = extract_video_id(s)
            if vid:
                vd = yt_api('videos', {'part': 'snippet', 'id': vid})
                if not vd.get('items'): return None
                cid = vd['items'][0]['snippet']['channelId']
                d = yt_api('channels', {'part': 'snippet,statistics', 'id': cid})
            else:
                m = re.search(r'@([\w.\-ぁ-んァ-ヶ一-龥ー]+)', s)
                if not m: return None
                d = yt_api('channels', {'part': 'snippet,statistics', 'forHandle': '@' + m.group(1)})
        if not d.get('items'): return None
        c = d['items'][0]
        return {
            'id': c['id'],
            'title': c['snippet']['title'],
            'subscribers': int(c.get('statistics', {}).get('subscriberCount', 0)),
            'total_videos': int(c.get('statistics', {}).get('videoCount', 0)),
            'total_views': int(c.get('statistics', {}).get('viewCount', 0)),
        }
    except Exception:
        return None


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_channel_videos(channel_id, max_results=25):
    """チャンネル分析用：直近動画の詳細リスト"""
    from datetime import timezone
    try:
        search_data = yt_api('search', {
            'part': 'id', 'channelId': channel_id,
            'order': 'date', 'type': 'video', 'maxResults': max_results
        })
        ids = [i['id']['videoId'] for i in search_data.get('items', []) if i['id'].get('videoId')]
        if not ids: return []
        vdata = yt_api('videos', {'part': 'snippet,statistics,contentDetails', 'id': ','.join(ids[:25])})
        now = datetime.now(timezone.utc)
        out = []
        for item in vdata.get('items', []):
            sn = item['snippet']; st_ = item.get('statistics', {})
            views = int(st_.get('viewCount', 0))
            likes = int(st_.get('likeCount', 0))
            pub_date = datetime.strptime(sn['publishedAt'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
            days = max(1, min(365, (now - pub_date).days))
            out.append({
                'id': item['id'],
                'title': sn['title'],
                'views': views,
                'likes': likes,
                'comments': int(st_.get('commentCount', 0)),
                'lr': likes / views * 100 if views > 0 else 0,
                'vpd': views / days,
                'days': (now - pub_date).days,
                'duration': parse_duration(item['contentDetails']['duration']),
                'hour_jst': (pub_date.hour + 9) % 24,
                'weekday': pub_date.weekday(),
                'published': sn['publishedAt'][:10],
            })
        return out
    except Exception:
        return []


# ════════════════════════════════════════════════════════
# 感情・タイトル判定の共通正規表現
# ════════════════════════════════════════════════════════

RE_EMO_SURPRISE = r'衝撃|驚き|まさか|信じられない|ヤバい|ヤバすぎ|えっ|実は|知らなかった|まじ|ガチ|本気'
RE_EMO_FEAR     = r'危険|NG|やってはいけない|失敗|後悔|損する|終わる|最悪|注意|警告|闇|怖'
RE_EMO_DESIRE   = r'稼げる|モテる|痩せる|お金|自由|成功|夢|理想|最強|無敵|すごい|神|爆伸び|億'
RE_EMO_EMPATHY  = r'あるある|わかる|共感|みんな|あなたも|こんな人|こんな経験|だけど|なのに'
RE_CURIOSITY    = r'理由|なぜ|実は|意外|本当に|本当は|したら.*だった|結果|真実|秘密|裏技|知られていない|実際|ぶっちゃけ|どうなる|検証'
RE_TARGET       = r'初心者|入門|上級|プロ|副業|社会人|学生|ビジネス|主婦|子ども|子供|シニア'

def emotional_count_of(title):
    return sum([
        bool(re.search(RE_EMO_SURPRISE, title)),
        bool(re.search(RE_EMO_FEAR, title)),
        bool(re.search(RE_EMO_DESIRE, title)),
        bool(re.search(RE_EMO_EMPATHY, title)),
    ])


# ════════════════════════════════════════════════════════
# 分析エンジン
# ════════════════════════════════════════════════════════

def analyze(v, bench=None):
    title    = v['title']
    desc     = v['description']
    tags     = v['tags']
    views    = v['views']
    likes    = v['likes']
    comments = v['comments']
    duration = v['duration']
    subs     = v['subscribers']
    days     = v['days_since']
    # 古い動画の日次再生が不当に低く出ないよう365日でキャップ
    eff_days = min(days, 365)
    vpd      = views / max(1, eff_days)
    lr       = likes / views * 100 if views > 0 else 0
    cr       = comments / views * 100 if views > 0 else 0

    scores = {}
    good, bad, critical, impr = [], [], [], []

    # タイトルから主要トピック語を抽出（キーワード判定に使用）
    title_nouns = re.findall(r'[ァ-ヶー]{3,}|[一-龥]{3,}', title)

    bench_avg = bench['avg_views'] if bench and bench['avg_views'] > 0 else None
    ch_ratio  = views / bench_avg if bench_avg else None

    # ────────────────────────────────
    # 実績証明済み判定（結果が正義）
    # ────────────────────────────────
    buzz_label = None
    if views >= 1_000_000:
        is_proven = True
        buzz_label = f"🔥 メガヒット動画（{views:,}再生）── 実績はすでに証明済み"
    elif views >= 100_000 and (ch_ratio is None or ch_ratio >= 1.0):
        is_proven = True
        buzz_label = f"🔥 ヒット動画（{views:,}再生）── 実績はすでに証明済み"
    elif ch_ratio and ch_ratio >= 3.0 and views >= 10_000:
        is_proven = True
        buzz_label = f"🔥 チャンネル内でバズ中（中央値の{ch_ratio:.1f}倍）── このテーマが刺さっている"
    elif ch_ratio and ch_ratio >= 2.0 and views >= 3_000:
        is_proven = True
        buzz_label = f"📈 チャンネル平均超え（中央値の{ch_ratio:.1f}倍）── 良い結果が出ている"
    else:
        is_proven = False

    # ────────────────────────────────
    # 0. 実績・人気度（最重要カテゴリ）
    # ────────────────────────────────
    perf = 0.0

    if views >= 5_000_000:
        perf += 4.0; good.append(f"再生数{views:,}回 → 全YouTubeでもトップ層の実績")
    elif views >= 1_000_000:
        perf += 3.7; good.append(f"再生数{views:,}回 → ミリオン達成。圧倒的な実績")
    elif views >= 100_000:
        perf += 3.2; good.append(f"再生数{views:,}回 → 10万超えの強い実績")
    elif views >= 10_000:
        perf += 2.5; good.append(f"再生数{views:,}回 → 1万超え。十分な実績")
    elif views >= 1_000:
        perf += 1.6
    elif views >= 100:
        perf += 0.8
    else:
        perf += 0.3
        bad.append(f"再生数がまだ少ない（{views:,}回）→ まずは露出を増やす段階")

    if ch_ratio is not None:
        if ch_ratio >= 5:
            perf += 3.0; good.append(f"チャンネル中央値の{ch_ratio:.1f}倍 → チャンネル史上級のバズ")
        elif ch_ratio >= 2:
            perf += 2.6; good.append(f"チャンネル中央値の{ch_ratio:.1f}倍 → 明確に当たっている動画")
        elif ch_ratio >= 1:
            perf += 2.0; good.append(f"チャンネル中央値以上の再生（{ch_ratio:.1f}倍）")
        elif ch_ratio >= 0.5:
            perf += 1.2
            bad.append(f"チャンネル中央値をやや下回る（{ch_ratio:.1f}倍）")
        else:
            perf += 0.4
            bad.append(f"チャンネル中央値を大きく下回る（{ch_ratio:.1f}倍）→ テーマかパッケージの見直しを")
            impr.append("【確認方法】YouTube Studio → アナリティクス → コンテンツ → 上位動画とこの動画のテーマ・タイトル型・サムネイルを比較し、当たっている型に寄せる")
    else:
        perf += 1.5

    if subs > 0 and views > 0:
        vr_ratio = views / subs
        if vr_ratio >= 3:
            perf += 3.0; good.append(f"再生数が登録者数の{vr_ratio:.1f}倍 → 外部・新規へ強く拡散している")
        elif vr_ratio >= 1:
            perf += 2.5; good.append(f"再生数が登録者数を超えている（{vr_ratio:.1f}倍）→ 新規リーチに成功")
        elif vr_ratio >= 0.3:
            perf += 1.8
        elif vr_ratio >= 0.1:
            perf += 1.0
        else:
            perf += 0.4
    else:
        perf += 1.0

    scores['実績'] = max(1, min(10, round(perf, 1)))

    # ────────────────────────────────
    # 1. タイトル
    # ────────────────────────────────
    ts = 10
    tlen = len(title)

    if tlen > 70:
        ts -= 3
        bad.append(f"タイトルが長すぎる（{tlen}文字）→ スマホでは40文字超えで見切れる")
        impr.append(f"【変更場所】YouTube Studio → コンテンツ → 該当動画 → 詳細タブ → タイトル欄。現在{tlen}文字を60文字以内に短縮する。伝えたいことを1つに絞り、残りは概要欄の1行目に移す")
    elif tlen < 20:
        if is_proven:
            ts -= 1
            good.append(f"短いタイトル（{tlen}文字）だが実績が出ている → 強いワード選びができている")
        else:
            ts -= 4
            critical.append(f"タイトルが短すぎる（{tlen}文字）→ 情報量が少なく検索にヒットしにくい")
            impr.append("【変更場所】YouTube Studio → コンテンツ → 詳細タブ → タイトル欄。「【初心者向け】〇〇を3分で〇〇する方法」のように「誰向け・何を・どうなる」の3要素で書き直す（30〜60文字目標）")
    elif tlen < 30:
        ts -= (1 if is_proven else 2)
        if not is_proven:
            bad.append(f"タイトルがやや短い（{tlen}文字）")
            impr.append("【変更場所】YouTube Studio → コンテンツ → 詳細タブ → タイトル欄。視聴者が得られる具体的な成果（例：「3分で完成」「登録者が増えた理由」）を付け加えて30文字以上にする")
    else:
        good.append(f"タイトルの文字数が適切（{tlen}文字）")

    if re.search(r'\d', title):
        good.append("タイトルに数字あり → クリック率が平均38%向上するデータがある")
    else:
        ts -= (1 if is_proven else 2)
        if not is_proven:
            bad.append("タイトルに数字がない → 具体性が薄くクリック率が低くなりやすい")
            impr.append("【変更場所】YouTube Studio → コンテンツ → 詳細タブ → タイトル欄。現タイトルに「3つの方法」「5つのコツ」「10分で完成」などの具体的な数字を1つ追加するだけでクリック率が大きく変わる")

    has_bracket  = bool(re.search(r'【.*?】|「.*?」|\[.*?\]', title))
    has_howto    = bool(re.search(r'方法|やり方|手順|仕方|コツ|ポイント|テクニック|ワザ', title))
    has_benefit  = bool(re.search(r'できる|わかる|なる|増える|減る|上がる|下がる|解決|改善|最速|完全', title))
    has_urgency  = bool(re.search(r'知らないと|損する|必見|絶対|今すぐ|要注意|危険|NG|やばい', title))
    has_target   = bool(re.search(RE_TARGET, title))
    has_question = bool(re.search(r'[？?]|なぜ|どうして|どうやって', title))
    hook_count   = sum([has_bracket, has_howto, has_benefit, has_urgency, has_target, has_question])

    if hook_count >= 3:
        good.append(f"タイトルのクリック誘引要素が強い（{hook_count}種類のフックあり）")
    elif hook_count == 2:
        good.append(f"タイトルにクリック誘引要素が{hook_count}つある")
    elif hook_count == 1:
        ts -= (1 if is_proven else 2)
        if not is_proven:
            bad.append("タイトルのクリック誘引要素が1つだけ → もっとクリックしたくなる表現を追加できる")
            impr.append("【変更場所】YouTube Studio → コンテンツ → 詳細タブ → タイトル欄。【】で対象者・数字・メリットを追加する。例：「【初心者向け】〇〇を5分でできる3つの方法」「〇〇を知らないと損！今すぐできる改善法」")
    else:
        if is_proven:
            ts -= 1
        else:
            ts -= 4
            critical.append("タイトルにクリックを誘う要素がない → 検索でヒットしても素通りされる")
            impr.append("【変更場所】YouTube Studio → コンテンツ → 詳細タブ → タイトル欄。パターン例①：「【完全版】〇〇を5分で自動化する3つの方法」②：「知らないと損！〇〇を劇的に改善する方法」③：「〇〇が難しい人向け｜ゼロから分かる入門ガイド」")

    if title_nouns:
        first_pos = title.find(title_nouns[0])
        if first_pos < len(title) // 2:
            good.append(f"タイトル前半にキーワードあり（{title_nouns[0]}）→ 検索エンジンは前半を優先評価")
        else:
            ts -= 1
            bad.append("キーワードがタイトル後半に寄っている → 最重要語を先頭に持ってくる")
            impr.append(f"【変更場所】YouTube Studio → コンテンツ → 詳細タブ → タイトル欄。「{title_nouns[0]}」をタイトルの先頭15文字以内に移動する。検索エンジンはタイトルの前半を重く評価するため最重要キーワードは必ず冒頭に置く")
    else:
        ts -= (1 if is_proven else 3)
        if not is_proven:
            critical.append("タイトルに具体的な検索キーワードがない → 検索から発見されない")
            impr.append("【変更場所】YouTube Studio → コンテンツ → 詳細タブ → タイトル欄。実際に検索される名詞（例：スプレッドシート・自動化・入力フォームなど）を先頭15文字以内に入れる。「〇〇 やり方」「〇〇 使い方」でGoogle検索して実際に出てくる言葉を使う")

    scores['タイトル'] = max(1, ts)

    # ────────────────────────────────
    # 2. サムネイル
    # ────────────────────────────────
    ths = 10
    if v['has_custom_thumb']:
        good.append("カスタムサムネイルが設定されている → CTRを上げる最重要施策")
    else:
        ths -= 7
        critical.append("カスタムサムネイルが未設定 → 自動生成の静止画のまま。CTRが2〜5倍変わる最大の改善点")
        impr.append("【手順①】canva.com → 「YouTubeサムネイル」テンプレを開く（1280×720px)。【デザイン要素】大きいテキスト（テーマを7文字以内）＋顔写真または驚きのアイコン＋赤・黄など高コントラスト背景。【アップロード場所】YouTube Studio → コンテンツ → 該当動画 → 「サムネイル」欄 → 「カスタムサムネイルをアップロード」")
    scores['サムネイル'] = max(1, ths)

    # ────────────────────────────────
    # 3. 概要欄
    # ────────────────────────────────
    ds = 10
    dlen = len(desc)
    if dlen == 0:
        ds -= (4 if is_proven else 8)
        if not is_proven:
            critical.append("概要欄が完全に空 → SEO的にほぼ存在しない動画扱い。即改善必須")
        else:
            bad.append("概要欄が空 → 実績は出ているが検索流入を取りこぼしている")
        impr.append("【変更場所】YouTube Studio → コンテンツ → 詳細タブ → 説明欄。構成例：「1行目：この動画で分かること（キーワード含む）／2〜5行目：内容の要約／チャプター（0:00〜）／▼チャンネル登録リンク／SNSリンク」最低300文字・理想800文字以上")
    elif dlen < 100:
        ds -= (3 if is_proven else 5)
        if not is_proven:
            critical.append(f"概要欄がほぼ空（{dlen}文字）→ 検索エンジンにコンテンツが伝わらない")
        else:
            bad.append(f"概要欄が短い（{dlen}文字）→ 検索流入の伸びしろあり")
        impr.append("【変更場所】YouTube Studio → コンテンツ → 詳細タブ → 説明欄の先頭を書き換える。「この動画では〇〇（メインキーワード）について解説します」のようにテーマを明示し、最初の125文字以内にキーワードを最低1回入れる（ここは「もっと見る」を押さずに全員が見る場所）")
    elif dlen < 300:
        ds -= 3
        bad.append(f"概要欄が短い（{dlen}文字）")
        impr.append("【変更場所】YouTube Studio → コンテンツ → 詳細タブ → 説明欄。動画の要約（200文字）＋タイムスタンプ＋チャンネル登録リンク＋関連動画・SNSリンクを追記して500文字以上にする")
    elif dlen < 600:
        ds -= 1
        bad.append(f"概要欄がやや短い（{dlen}文字）")
        impr.append("【変更場所】YouTube Studio → コンテンツ → 詳細タブ → 説明欄。現在の内容に加えて「この動画で学べること」「よくある質問」「関連リンク」を追記して800文字目標にする")
    else:
        good.append(f"概要欄が充実している（{dlen}文字）")

    if re.search(r'\d:\d\d', desc):
        good.append("タイムスタンプ（チャプター）が設定されている → UX向上＆検索評価アップ")
    elif duration > 3 * 60:
        ds -= 2
        bad.append("タイムスタンプ（チャプター）が未設定 → 視聴者が迷子になりやすく離脱率が上がる")
        impr.append("【変更場所】YouTube Studio → コンテンツ → 詳細タブ → 説明欄。必ず「0:00」から始めること（これがないと自動認識されない）。例：「0:00 はじめに\n1:30 〇〇とは\n3:00 実践手順\n5:30 まとめ」と書くだけでチャプターが自動生成される")

    if re.search(r'チャンネル登録|登録はこちら|subscribe', desc, re.I):
        good.append("概要欄にチャンネル登録リンクがある")
    else:
        ds -= 2
        bad.append("概要欄にチャンネル登録リンクがない → 流入を登録に繋げられていない")
        impr.append("【変更場所】YouTube Studio → コンテンツ → 詳細タブ → 説明欄の冒頭3行以内。例：「▼チャンネル登録はこちら↓\nhttps://www.youtube.com/@チャンネルID?sub_confirmation=1」※末尾に ?sub_confirmation=1 をつけると登録確認ポップアップが出て登録率が上がる")

    hashtags = re.findall(r'#\S+', desc)
    if len(hashtags) >= 3:
        good.append(f"ハッシュタグが{len(hashtags)}個設定されている")
    elif len(hashtags) > 0:
        ds -= 1
    else:
        ds -= 1
        if not is_proven:
            bad.append("ハッシュタグが0個 → ハッシュタグ検索からの流入機会ゼロ")
            impr.append("【変更場所】YouTube Studio → コンテンツ → 詳細タブ → 説明欄の一番末尾。関連ハッシュタグを3〜5個追加する。例：「#スプレッドシート #Excel #自動化 #Google #仕事効率化」タイトルのキーワードと連動させると検索ヒット率が上がる")

    if dlen >= 100:
        first_125 = desc[:125]
        if title_nouns and any(n in first_125 for n in title_nouns):
            good.append("概要欄の冒頭にタイトルキーワードが含まれている → SEO効果◎")
        else:
            ds -= 1
            bad.append("概要欄冒頭125文字にタイトルのキーワードがない → SEO的に勿体ない")
            impr.append("【変更場所】YouTube Studio → コンテンツ → 詳細タブ → 説明欄の1行目を書き換える。例：「この動画では〇〇（タイトルのメインキーワード）の使い方を初心者向けに解説します。」という形で125文字以内にキーワードを入れる")

    if re.search(r'https?://', desc):
        good.append("概要欄に外部リンクがある → SNSや関連動画への誘導になっている")
    elif dlen > 200:
        ds -= 1
        bad.append("概要欄に外部リンクがない → SNSや関連動画への誘導が弱い")
        impr.append("【変更場所】YouTube Studio → コンテンツ → 詳細タブ → 説明欄の中〜末尾。X(Twitter)・note・関連動画のURLを追記する。リンクがあると概要欄の情報量が増えSEO評価にも好影響")

    scores['概要欄'] = max(1, ds)

    # ────────────────────────────────
    # 4. タグ（現代のYouTubeでは影響小のため減点控えめ）
    # ────────────────────────────────
    tg = 10
    tc = len(tags)
    if tc == 0:
        tg -= (2 if is_proven else 5)
        if not is_proven:
            bad.append("タグが1個もない → 関連動画への掲出機会を一部捨てている（影響は小〜中）")
            impr.append("【変更場所】YouTube Studio → コンテンツ → 詳細タブ → 下にスクロール → 「タグ」欄。構成：①メインキーワード1語（例：スプレッドシート）②複合キーワード3〜5個（例：スプレッドシート 使い方、スプレッドシート 自動化）③関連ジャンル（例：Google, 仕事効率化, プログラミング）で計10〜15個入れる")
    elif tc < 5:
        tg -= 3
        bad.append(f"タグが少なめ（{tc}個）→ 検索・関連動画への表示機会が限られる")
        impr.append(f"【変更場所】YouTube Studio → コンテンツ → 詳細タブ → タグ欄。現在{tc}個から10個以上に増やす。追加候補：「〇〇 やり方」「〇〇 解説」「〇〇 初心者」「〇〇 入門」など動画テーマの複合語を入れる")
    elif tc < 10:
        tg -= 1
    elif tc > 30:
        tg -= 1
        bad.append(f"タグが多すぎる可能性（{tc}個）→ スパム判定のリスク")
        impr.append("【変更場所】YouTube Studio → コンテンツ → 詳細タブ → タグ欄。動画内容と直接関係しないタグ・「youtube」「動画」など一般的すぎるタグを削除して15〜20個に整理する")
    else:
        good.append(f"タグが適切に設定されている（{tc}個）")

    if tags:
        first_tag = tags[0]
        if len(first_tag) >= 3 and re.search(r'[ァ-ヶー一-龥a-zA-Z]', first_tag):
            good.append(f"最初のタグが検索キーワード（{first_tag}）→ 最重要タグを先頭に置くのは正解")
        long_tail = [t for t in tags if (' ' in t and len(t) >= 8) or len(t) >= 12]
        if len(long_tail) >= 3:
            good.append(f"ロングテールキーワードタグがある（{len(long_tail)}個）→ 競合少ない検索でヒットしやすい")

    scores['タグ'] = max(1, tg)

    # ────────────────────────────────
    # 5. エンゲージメント（チャンネル規模別の基準）
    # ────────────────────────────────
    if subs >= 1_000_000:
        t_great, t_good, t_ok, t_low = 4.0, 2.0, 1.0, 0.3
    elif subs >= 100_000:
        t_great, t_good, t_ok, t_low = 5.0, 2.5, 1.2, 0.4
    elif subs >= 10_000:
        t_great, t_good, t_ok, t_low = 6.0, 3.5, 1.8, 0.6
    else:
        t_great, t_good, t_ok, t_low = 8.0, 4.0, 2.0, 0.7

    es = 10
    bench_lr = bench['avg_like_rate'] if bench else None

    if views < 30:
        bad.append(f"再生数が少なすぎてエンゲージメントの正確な評価が難しい（{views}回）")
        es = 5
    else:
        bench_label = f"（チャンネル中央値{bench_lr:.1f}%）" if bench_lr else ""
        size_label = f"※登録者{subs:,}人規模の基準で評価"
        if lr >= t_great:
            good.append(f"いいね率が非常に高い（{lr:.1f}%）{bench_label} → 視聴者満足度が極めて高い証拠")
        elif lr >= t_good:
            good.append(f"いいね率が良好（{lr:.1f}%）{bench_label}{size_label}")
        elif lr >= t_ok:
            es -= 2
            bad.append(f"いいね率が低め（{lr:.1f}%）{bench_label}{size_label}")
            impr.append("【変更場所】動画本編を再編集して2か所にCTAを追加する。①冒頭30秒以内：「役に立ったらいいねボタンを押してもらえると励みになります」②動画の最後：「最後まで見てくれてありがとう！参考になったらいいねをポチっとお願いします」")
        elif lr >= t_low:
            es -= 4
            critical.append(f"いいね率が低い（{lr:.1f}%）{bench_label} → 視聴者が満足していないかCTAが弱い")
            impr.append("【変更場所】動画本編を再編集。「いいねをお願いします」ではなく「○○ができた方はいいねで教えてください！」のように視聴者の行動に紐づけた言い方に変える。解決策を示した直後と終了直前の2か所に入れる")
        else:
            es -= 6
            critical.append(f"いいね率が極端に低い（{lr:.1f}%）→ コンテンツ品質かCTAに根本的な問題がある")
            impr.append("【確認方法】YouTube Studio → アナリティクス → この動画 → エンゲージメント → 視聴者維持率グラフを開く。グラフが急落している時点の動画を確認し、テンポ・音質・内容のズレを特定して修正する")

        if bench_lr and lr > 0:
            if lr >= bench_lr * 1.5:
                good.append(f"このチャンネルの中でいいね率がトップクラス（{lr:.1f}% vs 中央値{bench_lr:.1f}%）")
            elif lr < bench_lr * 0.6:
                es -= 1
                bad.append(f"このチャンネルの他の動画よりいいね率が低い（{lr:.1f}% vs 中央値{bench_lr:.1f}%）")
                impr.append("【確認方法】YouTube Studio → アナリティクス → インプレッションのクリック率を確認。この動画のCTRがチャンネル平均より低ければサムネイル・タイトルの問題。【変更場所】YouTube Studio → コンテンツ → サムネイルとタイトルを変更して48時間アナリティクスで効果を確認する")

        if cr >= 1.0:
            good.append(f"コメント率が高い（{cr:.2f}%）→ 視聴者を巻き込めている")
        elif cr >= 0.2:
            good.append(f"コメントが一定数ある（{cr:.2f}%）")
        elif cr > 0:
            es -= 1
            bad.append(f"コメント率が低い（{cr:.2f}%）")
            impr.append("【変更場所】動画本編の終了15〜30秒前に問いかけセリフを追加して再編集。例：「あなたはどのやり方でやってますか？コメントで教えてください！」「○○が難しかった人はどこが一番詰まりましたか？コメントで教えてもらえると次の動画で解説します」")
        else:
            es -= 2
            bad.append("コメントが0件 → コミュニティとして機能していない")
            impr.append("【今すぐできること①】YouTube Studio → コンテンツ → この動画のコメント欄に自分でコメントして固定する（コメント右の「…」→「固定」）。【今すぐできること②】動画本編の最後に「コメントに全部返信します！」と追加して再編集する")

        if likes > 0 and comments > 0:
            lc = likes / comments
            if 5 <= lc <= 50:
                good.append(f"いいね/コメント比が健全（{lc:.0f}:1）→ 議論が活発")

    scores['エンゲージメント'] = max(1, es)

    # ────────────────────────────────
    # 6. 視聴ペース
    # ────────────────────────────────
    ps = 10
    bench_vpd = bench['avg_vpd'] if bench else None

    if days <= 1:
        bad.append("投稿直後のため視聴ペースの評価は参考程度")
        ps = 7
    elif is_proven and views >= 100_000:
        good.append(f"累計{views:,}再生の実績 → 視聴ペースの細かい増減より総量が物語っている")
    else:
        vpd_str = f"{vpd:.0f}回/日"
        if bench_vpd:
            ratio = vpd / bench_vpd if bench_vpd > 0 else 0
            if ratio >= 2.0:
                good.append(f"このチャンネルの中でバズっている（{vpd_str}、チャンネル中央値の{ratio:.1f}倍）")
            elif ratio >= 1.2:
                good.append(f"チャンネル中央値より良いペース（{vpd_str}、{ratio:.1f}倍）")
            elif ratio >= 0.7:
                ps -= 2
                bad.append(f"チャンネル中央値並みのペース（{vpd_str}）→ 伸ばす余地あり")
                impr.append("【手順】①YouTube Studio → コミュニティ → 「投稿を作成」でこの動画のリンクを告知 ②X(Twitter)・Instagramに動画URLと見どころを投稿する。投稿後48時間が最もアルゴリズム評価に影響するため、公開直後の拡散が最重要")
            elif ratio >= 0.3:
                ps -= 4
                bad.append(f"チャンネル中央値を下回る視聴ペース（{vpd_str}、{ratio:.1f}倍）")
                impr.append("【変更場所】YouTube Studio → コンテンツ → 該当動画 → 詳細タブ → タイトル欄・サムネイル欄を変更する。変更後48〜72時間、アナリティクスのCTR（インプレッションのクリック率）を監視して2%以下なら再変更する")
            else:
                ps -= 6
                critical.append(f"チャンネル中央値と比べて著しく再生されていない（{vpd_str}、{ratio:.1f}倍）")
                impr.append("【確認方法】YouTube Studio → アナリティクス → コンテンツ → 「視聴者が他に見た動画」を確認。自分のチャンネルで再生数が多い動画のテーマ・構成・長さの共通点を書き出し、次の動画に取り入れる")
        else:
            if vpd >= 500:
                good.append(f"再生ペースが良好（{vpd_str}）")
            elif vpd >= 100:
                good.append(f"安定した再生ペース（{vpd_str}）")
            elif vpd >= 20:
                ps -= 2
                bad.append(f"再生ペースが低い（{vpd_str}）")
                impr.append("【変更場所（次回から）】YouTube Studio → アップロード → 「スケジュール」タブ → 平日火〜木曜の19〜21時に予約投稿する。既存の動画の投稿時刻は変更できないため、次回以降に必ず適用する")
            else:
                ps -= 4
                bad.append(f"再生ペースが非常に低い（{vpd_str}）")
                impr.append("【優先順位】①サムネイル変更（最も即効性が高い）→ YouTube Studio → コンテンツ → サムネイル欄 ②タイトル変更 → 詳細タブ → タイトル欄 ③タグ追加 → タグ欄。変更後48時間ごとにアナリティクスのCTRで効果を確認する")

        if subs > 0 and views > 0 and not is_proven:
            vr = views / subs * 100
            if vr >= 300:
                good.append(f"再生数が登録者数の{vr:.0f}%（投稿{days}日後）→ 外部流入・拡散が起きている")
            elif vr >= 100:
                good.append(f"再生数が登録者数を超えている（{vr:.0f}%・投稿{days}日後）")
            elif vr >= 30:
                ps -= 1
                bad.append(f"再生数が登録者数の{vr:.0f}%（投稿{days}日後）→ 登録者に届いていない")
                impr.append("【今すぐできること】YouTube Studio → コミュニティ → 「投稿を作成」でこの動画への告知コメントを出す。登録者のコメントや問い合わせを見て「登録者が何に悩んでいるか」を再確認し、次の動画タイトルに反映させる")
            elif vr >= 10:
                ps -= 3
                bad.append(f"再生数が登録者数の{vr:.0f}%と低い（投稿{days}日後）")
                impr.append("【変更場所①】YouTube Studio → コミュニティ → 投稿作成でこの動画の告知を出す。【変更場所②（次回から）】YouTube Studio → アップロード → スケジュール設定で平日19〜21時に予約投稿する")
            else:
                ps -= 5
                critical.append(f"登録者に対して再生数が極端に少ない（{vr:.0f}%・投稿{days}日後）")
                impr.append("【確認方法】YouTube Studio → アナリティクス → 視聴者 → 「視聴者が他に見た動画」。そこに出てくる動画とこのチャンネルのテーマが大きく違う場合、登録者の期待とコンテンツがずれている。直近5本のコメントを読んで「何を求めているか」を整理する")

    scores['視聴ペース'] = max(1, ps)

    # ────────────────────────────────
    # 7. 動画の長さ
    # ────────────────────────────────
    durs = 10
    dm = duration // 60
    bench_dur = bench['avg_duration'] if bench else None

    if v['is_short']:
        durs = 8
        good.append("Shorts動画 → アルゴリズム拡散に有利。新規流入経路として重要")
    elif duration < 3 * 60:
        durs -= (1 if is_proven else 4)
        if not is_proven:
            bad.append(f"動画が短すぎる（{dm}分{duration%60:02d}秒）→ チュートリアル系として物足りない")
            impr.append(f"【次回動画から適用】現在{dm}分の内容に「よくある質問」「応用編」「失敗例の解説」を追加して8〜15分構成に再設計する。公開済みのこの動画は変更不可のため次回以降に活かす")
    elif 7 * 60 <= duration <= 20 * 60:
        good.append(f"動画の長さが最適ゾーン（{dm}分）→ 視聴維持率と評価のバランスが良い")
    elif duration < 7 * 60:
        durs -= (1 if is_proven else 2)
        if not is_proven:
            bad.append(f"動画がやや短い（{dm}分）→ 7〜15分が最も視聴維持率・評価ともに高い")
            impr.append(f"【次回動画から適用】現在{dm}分の構成に「よくある失敗例」「上級者向けTips」「Q&Aコーナー」などを加えて7〜15分に伸ばす。公開済みの動画は差し替え不可のため今後の動画設計に反映する")
    elif duration > 25 * 60:
        durs -= (1 if is_proven else 3)
        if not is_proven:
            bad.append(f"動画が長すぎる（{dm}分）→ 25分超えは途中離脱が急増する")
            impr.append(f"【今すぐできること】YouTube Studio → コンテンツ → 詳細タブ → 説明欄にタイムスタンプ（チャプター）を追加して視聴者が飛べるようにする。【次回から】{dm}分超えのネタは前編・後編に分けて2本投稿する")

    if bench_dur and not v['is_short'] and bench_dur > 0:
        dur_diff = duration - bench_dur
        if abs(dur_diff) > 5 * 60 and not is_proven:
            direction = "長い" if dur_diff > 0 else "短い"
            bad.append(f"チャンネル中央値より{abs(dur_diff)//60}分{direction}（中央値{int(bench_dur//60)}分）→ 視聴者の期待とズレる可能性")

    scores['動画の長さ'] = max(1, durs)

    # ────────────────────────────────
    # 8. 投稿タイミング
    # ────────────────────────────────
    tms = 10
    hour    = v.get('pub_jst_hour', 12)
    weekday = v.get('pub_weekday', 1)
    wd_names = ['月', '火', '水', '木', '金', '土', '日']
    wd_name = wd_names[weekday] if weekday < len(wd_names) else '?'

    if 19 <= hour <= 22:
        good.append(f"投稿時間が最適ゾーン（JST {hour}時台）→ 帰宅後の視聴ピーク時間帯")
    elif 12 <= hour <= 14:
        good.append(f"投稿時間がランチタイム（JST {hour}時台）→ 昼休み視聴層に届きやすい")
    elif 7 <= hour <= 9:
        tms -= 1
        if not is_proven:
            bad.append(f"投稿時間が朝（JST {hour}時台）→ 通勤中は動画視聴が少ない")
            impr.append("【変更場所（次回から）】YouTube Studio → アップロード → 「スケジュール」タブ → 平日19〜21時に日時設定して予約投稿する。既に公開済みのこの動画の投稿時刻は変更できないため次回から必ず予約投稿機能を使う")
    elif hour < 7 or hour >= 23:
        tms -= (1 if is_proven else 3)
        if not is_proven:
            bad.append(f"投稿時間が深夜〜早朝（JST {hour}時台）→ 投稿直後の初動が全く取れない")
            impr.append("【変更場所（次回から）】YouTube Studio → アップロード → 「スケジュール」タブ → 平日火〜木曜の19〜21時を指定して予約投稿する。この動画の投稿時刻変更は不可のため、今すぐコミュニティ投稿で告知するのが次善策")
    else:
        tms -= 1

    if weekday in [1, 2, 3]:
        good.append(f"投稿曜日が最適（{wd_name}曜日）→ 週の中盤は競合が少なく埋もれにくい")
    elif weekday in [0, 4]:
        good.append(f"投稿曜日が良好（{wd_name}曜日）")
    elif weekday == 5:
        tms -= 1
        if not is_proven:
            bad.append("土曜日投稿 → 週末は視聴数は多いが通知が埋もれやすい")
            impr.append("【変更場所（次回から）】YouTube Studio → アップロード → 「スケジュール」で火〜木曜の19〜21時を指定して予約投稿する。土日はアクティブユーザーが多いが大手チャンネルの投稿も増えるため通知が埋もれやすい")
    else:
        tms -= 2
        if not is_proven:
            bad.append("日曜日投稿 → 月曜朝に通知が大量のメールに埋もれる")
            impr.append("【変更場所（次回から）】YouTube Studio → アップロード → 「スケジュール」で火〜木曜の19〜21時を指定して予約投稿する。日曜投稿は月曜朝の通知ラッシュに埋もれるため最も初動が取れにくい曜日")

    scores['投稿タイミング'] = max(1, tms)

    # ────────────────────────────────
    # 9. 収益・成長ポテンシャル
    # ────────────────────────────────
    rev = 10

    if not v['is_short']:
        if duration >= 8 * 60:
            good.append("8分以上 → ミッドロール広告が入れられる（収益最大化）")
        elif duration >= 3 * 60:
            rev -= 3
            bad.append("8分未満 → ミッドロール広告なし（収益を取りこぼしている）")
            impr.append("【次回動画から適用】現在の構成に「よくある質問・応用テクニック・失敗例」を加えて8分超えを目指す。8分以上になると動画途中にミッドロール広告が挿入でき収益が大幅に変わる。設定場所：YouTube Studio → コンテンツ → 収益化 → 広告の種類 → 「動画内広告」をオン")
        else:
            rev -= 5
            bad.append("動画が短すぎてプリロール広告1本しか入らない")

    if re.search(r'2024|2025|2026|最新|新機能|アップデート', title + desc[:100]):
        good.append("トレンドキーワードがある → 時事検索からの流入が見込める")

    if views > 100 and subs > 0 and views / subs > 2:
        good.append("外部流入が多く、新規登録者獲得チャンスが高い動画")

    if views > 0 and lr >= t_good and cr >= 0.2:
        good.append("エンゲージメントが高く、YouTubeアルゴリズムに好かれやすい状態")

    scores['収益・成長'] = max(1, rev)

    # ────────────────────────────────
    # 10. 視聴者設定・ターゲット精度
    # ────────────────────────────────
    tgt = 10

    has_target_word = bool(re.search(RE_TARGET, title))
    has_pain        = bool(re.search(r'できない|わからない|困って|解決|悩み|問題|失敗|NG|難しい|苦手', title + desc[:200]))
    has_desire      = bool(re.search(r'稼ぐ|増やす|早く|楽に|自動|効率|時短|節約|簡単|すごい|驚き', title + desc[:200]))
    has_value_prop  = bool(re.search(r'分で|ステップ|方法|コツ|ポイント|解説|やり方|手順|完全|徹底', title))

    if has_target_word:
        good.append("タイトルにターゲット読者が明示されている → 自分ごとと感じてもらいやすい")
    else:
        tgt -= (1 if is_proven else 2)
        if not is_proven:
            bad.append("タイトルに『誰向けか』が不明 → 視聴者は『自分のための動画か』判断できない")
            impr.append("【変更場所】YouTube Studio → コンテンツ → 詳細タブ → タイトル欄。タイトルの先頭に「【初心者向け】」「【副業したい人必見】」など対象者を【】で明示する。誰向けかが一目でわかると自分ごとに感じてもらいやすくCTRが上がる")

    if has_pain or has_desire:
        good.append("タイトルに視聴者の悩みや欲求が反映されている")
    else:
        tgt -= (1 if is_proven else 3)
        if not is_proven:
            critical.append("タイトルが視聴者の悩み・欲求と繋がっていない → 『自分ごと』として見てもらえない")
            impr.append("【変更場所】YouTube Studio → コンテンツ → 詳細タブ → タイトル欄。現タイトルに視聴者の悩みや欲求を追加する。例：「〇〇が難しい人へ」「〇〇がうまくいかない理由」「〇〇したい人が最初にやるべきこと」のように感情に直接刺さる言葉を入れる")

    if has_value_prop:
        good.append("動画で得られる価値がタイトルから伝わる")
    else:
        tgt -= (1 if is_proven else 2)
        if not is_proven:
            bad.append("タイトルから『何が得られるか』が伝わらない → 視聴する理由がない")
            impr.append("【変更場所】YouTube Studio → コンテンツ → 詳細タブ → タイトル欄。「〇〇ができるようになる」「〇〇が3分で完成する」「〇〇の悩みが即解決」など動画を見た後の具体的な変化をタイトルに入れる")

    scores['視聴者設定'] = max(1, tgt)

    # ────────────────────────────────
    # 11. チャンネル体制・一貫性
    # ────────────────────────────────
    cs = 10
    total_vids = v.get('total_channel_videos', 0)

    if total_vids == 0:
        cs = 5
    elif total_vids < 10:
        cs -= 3
        bad.append(f"チャンネル動画数が少ない（{total_vids}本）→ 信頼感・蓄積が足りない")
        impr.append("【行動目標】週1本ペースで30本を目指す。動画30本を超えるとYouTubeが「アクティブチャンネル」として評価し検索・関連動画への露出が増える。YouTube Studio → コンテンツ → 右上の「カレンダー」アイコンで投稿スケジュールを確認・管理できる")
    elif total_vids < 30:
        cs -= 1
        bad.append(f"動画数がまだ少ない（{total_vids}本）→ もう少しで本格的な蓄積になる")
        impr.append("【行動目標】週1本ペースを続けて50本の壁を超える。50本以上になると過去動画からの検索流入が安定し始める。次の3本分のテーマを事前に決めておくと継続しやすい")
    elif total_vids < 100:
        good.append(f"動画が着実に蓄積されている（{total_vids}本）")
    else:
        good.append(f"豊富な動画ライブラリがある（{total_vids}本）→ SEO的に有利")

    if bench:
        avg_v = bench['avg_views']
        max_v = bench['max_views']
        if avg_v > 0:
            cv = max_v / avg_v
            if cv <= 3:
                good.append(f"チャンネルの再生数が安定している（最高/中央値比: {cv:.1f}倍）→ コンテンツ品質が均質")
            elif cv <= 8:
                cs -= 1
            else:
                cs -= 2
                bad.append(f"再生数の格差が大きい（最高{max_v:,.0f}、中央値{avg_v:,.0f}）→ ヒットの再現性を高める余地")
                impr.append("【確認方法】YouTube Studio → アナリティクス → 最高再生動画を開く → 「視聴者維持率」「クリック率」「流入元」を確認。どこから人が来てどこで離脱していないかを把握し、同じタイトル型・テーマ・サムネイルパターンで続編・シリーズ動画を作る")

    scores['チャンネル体制'] = max(1, cs)

    # ────────────────────────────────
    # 12. バズ・バイラル適合度
    # ────────────────────────────────
    bz = 10
    emotional_count = emotional_count_of(title)

    if emotional_count >= 2:
        good.append(f"タイトルに感情トリガーが{emotional_count}種類ある → 見た瞬間に感情が動きクリックされやすい")
    elif emotional_count == 1:
        bz -= (1 if is_proven else 2)
        if not is_proven:
            bad.append("感情トリガーが1つだけ → タイトルを見ても感情が動かず素通りされる")
            impr.append("【変更場所】YouTube Studio → コンテンツ → タイトル欄。感情トリガー4パターン：①驚き「まさか〇〇が…」②恐怖「〇〇しないと後悔する理由」③欲求「〇〇だけで稼げる方法」④共感「〇〇あるある10選」から選んで現タイトルに追加する")
    else:
        if is_proven:
            bz -= 1
        else:
            bz -= 4
            critical.append("感情トリガーが0 → タイトルを見て感情が動かないためクリックされにくい。大きな改善ポイント")
            impr.append("【変更場所】YouTube Studio → コンテンツ → タイトル欄。今すぐ感情ワードを入れる：①「まさか〇〇が…衝撃の結果」②「〇〇してはいけない3つの理由」③「〇〇するだけで結果が変わる方法」④「〇〇あるある！共感した人いいねして」")

    curiosity_gap = bool(re.search(RE_CURIOSITY, title))
    if curiosity_gap:
        good.append("タイトルに「情報ギャップ」がある → 答えを知りたくてクリックされる構造")
    else:
        bz -= (1 if is_proven else 2)
        if not is_proven:
            bad.append("「続きが気になる」構造がない → 「見なくていいか」と思われてしまう")
            impr.append("【変更場所】YouTube Studio → コンテンツ → タイトル欄。情報ギャップパターン：「〇〇した結果→まさかの展開」「実は〇〇には理由があった」「〇〇を続けた3ヶ月後…」のように答えを見たいと思わせる構造にする")

    shareable = bool(re.search(r'保存版|完全版|永久保存|決定版|初公開|最安|無料|タダ|禁断|裏技|非公開|世界一|日本一|限定', title + desc[:100]))
    if shareable:
        good.append("「保存・シェアしたくなる」要素がある → 拡散されやすい")
    else:
        bz -= 1
        if not is_proven:
            bad.append("「誰かに見せたくなる」要素が弱い → 自然な拡散が起きにくい")
            impr.append("【変更場所】YouTube Studio → タイトル欄か説明欄の冒頭。「保存版」「完全版」「永久保存版」などを入れると「あとで見返したい・シェアしたい」という気持ちが生まれる")

    if bench:
        avg_v = bench['avg_views']
        if avg_v > 0:
            if views > avg_v * 2.0 and days >= 7:
                good.append(f"チャンネル内でトップクラスの伸び（中央値の{views/avg_v:.1f}倍）→ このテーマ・型が視聴者に刺さっている証拠")
            elif views < avg_v * 0.4 and days >= 14:
                bz -= 2
                critical.append(f"このテーマがチャンネルの視聴者層と合っていない可能性が高い（チャンネル中央値の{views/avg_v:.1f}倍）")
                impr.append("【確認方法】YouTube Studio → アナリティクス → 視聴者 → 「視聴者の興味・関心」を確認。視聴者が興味を持っているジャンルとこの動画のテーマがずれていないか確認する")

    scores['バズ適合度'] = max(1, bz)

    # ════════════════════════════════════════════════════════
    # バズる動画の共通パターンチェック（12項目）
    # ════════════════════════════════════════════════════════
    _has_chapter  = bool(re.search(r'\d:\d\d', desc))
    _has_cta_link = bool(re.search(r'チャンネル登録|登録はこちら|subscribe', desc, re.I))
    _has_number   = bool(re.search(r'\d', title))
    _good_time    = (19 <= v.get('pub_jst_hour', 0) <= 22) or (12 <= v.get('pub_jst_hour', 0) <= 14)
    dlen = len(desc)
    tc = len(tags)

    patterns = [
        {'label': '感情トリガーワードがある',
         'met': emotional_count >= 2,
         'detail': f'{emotional_count}種類',
         'why': '感情が動かないとクリックされない',
         'fix': 'YouTube Studio → コンテンツ → タイトル欄。4パターンから選ぶ：①驚き「まさか〇〇が…衝撃の結果」②恐怖「〇〇しないと後悔する理由」③欲求「〇〇するだけで稼げる方法」④共感「〇〇あるある10選」。今のタイトルに1語追加するだけでOK'},
        {'label': '情報ギャップ（続きが気になる）',
         'met': curiosity_gap,
         'detail': 'あり' if curiosity_gap else 'なし',
         'why': '「答えを知りたい」構造がないと素通りされる',
         'fix': 'YouTube Studio → コンテンツ → タイトル欄。例：「〇〇した結果→まさかの展開」「実は〇〇には理由があった」「〇〇を続けた3ヶ月後…」のように答えを見たいと思わせる構造にする'},
        {'label': 'カスタムサムネイル設定済み',
         'met': v['has_custom_thumb'],
         'detail': 'あり' if v['has_custom_thumb'] else '自動生成のまま',
         'why': 'CTRが2〜5倍変わる最大の改善点',
         'fix': '①canva.com → 「YouTubeサムネイル」テンプレ(1280×720px)を開く ②大きいテキスト(7文字以内)＋顔写真＋高コントラスト背景でデザイン ③YouTube Studio → コンテンツ → 該当動画 → サムネイル欄 → カスタムサムネイルをアップロード'},
        {'label': 'タイトルに数字がある',
         'met': _has_number,
         'detail': 'あり' if _has_number else 'なし',
         'why': 'クリック率が平均38%向上するデータがある',
         'fix': 'YouTube Studio → コンテンツ → タイトル欄。「3つの方法」「5つのコツ」「10分で完成」「2倍速くなる」などの数字を1つ追加するだけでOK'},
        {'label': 'ターゲット（誰向けか）が明示',
         'met': has_target_word,
         'detail': 'あり' if has_target_word else 'なし',
         'why': '「自分向けの動画だ」と感じてもらうために必須',
         'fix': 'YouTube Studio → コンテンツ → タイトル欄。先頭に「【初心者向け】」「【副業したい人必見】」「【スプレッドシート初心者】」など対象者を【】で明示する'},
        {'label': '概要欄300文字以上',
         'met': dlen >= 300,
         'detail': f'{dlen}文字',
         'why': 'SEO的に存在しない動画扱いになる',
         'fix': f'YouTube Studio → コンテンツ → 詳細タブ → 説明欄。現在{dlen}文字→300文字以上に増やす。構成：①動画の要約(1〜3行) ②タイムスタンプ ③チャンネル登録リンク ④SNS・関連動画リンク ⑤ハッシュタグ3〜5個'},
        {'label': 'タイムスタンプ（チャプター）設定',
         'met': _has_chapter,
         'detail': 'あり' if _has_chapter else 'なし',
         'why': '視聴維持率とSEO評価が上がる',
         'fix': 'YouTube Studio → コンテンツ → 詳細タブ → 説明欄。必ず「0:00」から始める（これがないと自動認識されない）。例：「0:00 はじめに\\n1:30 〇〇とは\\n3:00 実践手順\\n5:30 まとめ」'},
        {'label': 'チャンネル登録リンクあり',
         'met': _has_cta_link,
         'detail': 'あり' if _has_cta_link else 'なし',
         'why': '見た人を登録者に変える最も簡単な施策',
         'fix': 'YouTube Studio → コンテンツ → 詳細タブ → 説明欄の冒頭3行以内に追加。「▼チャンネル登録はこちら↓\\nhttps://www.youtube.com/@チャンネルID?sub_confirmation=1」※末尾の?sub_confirmation=1で登録確認ポップアップが出て登録率UP'},
        {'label': 'タグ10個以上',
         'met': tc >= 10,
         'detail': f'{tc}個',
         'why': '関連動画・検索への掲出機会が増える（影響は小〜中）',
         'fix': f'YouTube Studio → コンテンツ → 詳細タブ → タグ欄。現在{tc}個→10個以上に増やす。追加候補：「〇〇 使い方」「〇〇 解説 初心者」「〇〇 やり方」など複合語タグを3〜5個追加する'},
        {'label': f'いいね率{t_good}%以上（規模考慮）',
         'met': (lr >= t_good) if views >= 30 else None,
         'detail': f'{lr:.1f}%' if views >= 30 else '再生数少ない',
         'why': 'アルゴリズムが「良い動画」と判断する目安（登録者規模で基準調整済み）',
         'fix': '動画本編を再編集して2か所にCTAを追加。①冒頭30秒以内：「役に立ったらいいねを押してもらえると励みになります」②終了直前：「参考になったらいいねをポチっとお願いします！」'},
        {'label': '8分以上 or Shorts',
         'met': duration >= 8*60 or v['is_short'],
         'detail': 'Shorts' if v['is_short'] else f'{duration//60}分{duration%60:02d}秒',
         'why': '8分以上でミッドロール広告対応・収益最大化',
         'fix': '次回動画から構成に「よくある質問コーナー」「応用編」「失敗例の解説」を追加して8分超えを目指す。8分以上になったらYouTube Studio → 収益化 → 広告の種類 → 「動画内広告」をオンにする'},
        {'label': '投稿時間が最適帯（19〜22時 or 12〜14時）',
         'met': _good_time,
         'detail': f'JST {v.get("pub_jst_hour", 0)}時台',
         'why': '初動の再生数がアルゴリズム評価を決める',
         'fix': 'YouTube Studio → アップロード → 「スケジュール」タブ → 平日火〜木曜の19〜21時を指定して予約投稿する。既存動画の投稿時刻変更は不可のため次回から適用'},
    ]

    # ════════════════════════════════════════════════════════
    # 総合スコア（実績を最重要視）
    # ════════════════════════════════════════════════════════
    weights = {
        '実績': 3.5,
        'タイトル': 1.8, 'サムネイル': 1.4, '概要欄': 1.0, 'タグ': 0.4,
        'エンゲージメント': 1.6, '視聴ペース': 1.4, '動画の長さ': 0.6,
        '投稿タイミング': 0.5, '収益・成長': 0.5,
        '視聴者設定': 0.9, 'チャンネル体制': 0.5, 'バズ適合度': 1.4,
    }
    total = round(sum(scores[k] * weights[k] for k in scores) / sum(weights.values()) * 10)

    # 実績証明済みオーバーライド（結果が正義）
    if views >= 1_000_000:
        total = max(total, 88)
    elif views >= 100_000 and (ch_ratio is None or ch_ratio >= 1.0):
        total = max(total, 80)
    elif ch_ratio and ch_ratio >= 3.0 and views >= 10_000:
        total = max(total, 76)
    elif ch_ratio and ch_ratio >= 2.0 and views >= 3_000:
        total = max(total, 70)
    total = min(total, 100)

    if is_proven and critical:
        bad = critical + bad
        critical = []

    # ── 2軸スコア（実績 vs パッケージ） ──
    perf_keys = ['実績', 'エンゲージメント', '視聴ペース']
    pack_keys = [k for k in scores if k not in perf_keys]
    perf_w = {k: weights[k] for k in perf_keys}
    pack_w = {k: weights[k] for k in pack_keys}
    perf_score = round(sum(scores[k] * perf_w[k] for k in perf_keys) / sum(perf_w.values()) * 10)
    pack_score = round(sum(scores[k] * pack_w[k] for k in pack_keys) / sum(pack_w.values()) * 10)

    # ── 改善アクション重複排除 ──
    _seen = {}
    _deduped = []
    for _item in impr:
        if 'タイトル欄' in _item:
            _n = _seen.get('title', 0)
            if _n < 2:
                _seen['title'] = _n + 1
                _deduped.append(_item)
        elif 'タグ欄' in _item:
            if 'tags' not in _seen:
                _seen['tags'] = 1; _deduped.append(_item)
        elif 'サムネイル' in _item and ('アップロード' in _item or 'Canva' in _item or 'canva' in _item):
            if 'thumb' not in _seen:
                _seen['thumb'] = 1; _deduped.append(_item)
        elif '動画本編' in _item:
            if 'video' not in _seen:
                _seen['video'] = 1; _deduped.append(_item)
        elif 'スケジュール' in _item:
            if 'sched' not in _seen:
                _seen['sched'] = 1; _deduped.append(_item)
        elif '行動目標' in _item:
            if 'goal' not in _seen:
                _seen['goal'] = 1; _deduped.append(_item)
        elif '次回動画から' in _item:
            if 'next' not in _seen:
                _seen['next'] = 1; _deduped.append(_item)
        elif 'コミュニティ' in _item:
            if 'comm' not in _seen:
                _seen['comm'] = 1; _deduped.append(_item)
        else:
            _deduped.append(_item)
    impr = _deduped

    return {
        'scores': scores, 'total': total,
        'perf_score': perf_score, 'pack_score': pack_score,
        'is_proven': is_proven, 'buzz_label': buzz_label,
        'good': good, 'bad': bad, 'critical': critical, 'impr': impr,
        'like_rate': lr, 'comment_rate': cr, 'views_per_day': vpd,
        'bench': bench, 'patterns': patterns,
    }


def grade_of(total):
    if total >= 90: return 'S', '🏆'
    if total >= 80: return 'A', '🥇'
    if total >= 70: return 'B', '🥈'
    if total >= 55: return 'C', '🥉'
    if total >= 40: return 'D', '📉'
    return 'E', '🆘'


# ════════════════════════════════════════════════════════
# タイトルシミュレーター
# ════════════════════════════════════════════════════════

def evaluate_title_draft(title):
    """タイトル案を即採点（10点満点＋チェックリスト）"""
    checks = []
    score = 0.0
    tlen = len(title)

    ok_len = 30 <= tlen <= 60
    checks.append((ok_len, f"文字数 {tlen}文字（推奨：30〜60文字）"))
    score += 2.0 if ok_len else (1.0 if 20 <= tlen <= 70 else 0)

    has_num = bool(re.search(r'\d', title))
    checks.append((has_num, "数字が入っている（CTR +38%）"))
    score += 1.5 if has_num else 0

    emo = emotional_count_of(title)
    checks.append((emo >= 2, f"感情トリガー {emo}種類（推奨：2種類以上）"))
    score += min(2.0, emo * 1.0)

    cur = bool(re.search(RE_CURIOSITY, title))
    checks.append((cur, "情報ギャップ（続きが気になる構造）"))
    score += 1.5 if cur else 0

    tgt = bool(re.search(RE_TARGET, title))
    checks.append((tgt, "ターゲット（誰向けか）の明示"))
    score += 1.0 if tgt else 0

    hooks = sum([
        bool(re.search(r'【.*?】|「.*?」|\[.*?\]', title)),
        bool(re.search(r'方法|やり方|手順|仕方|コツ|ポイント|テクニック|ワザ', title)),
        bool(re.search(r'できる|わかる|なる|増える|減る|上がる|下がる|解決|改善|最速|完全', title)),
        bool(re.search(r'知らないと|損する|必見|絶対|今すぐ|要注意|危険|NG|やばい', title)),
        bool(re.search(r'[？?]|なぜ|どうして|どうやって', title)),
    ])
    checks.append((hooks >= 2, f"クリック誘引フック {hooks}種類（推奨：2種類以上）"))
    score += min(1.5, hooks * 0.75)

    nouns = re.findall(r'[ァ-ヶー]{3,}|[一-龥]{3,}|[a-zA-Z]{4,}', title)
    kw_front = bool(nouns) and title.find(nouns[0]) < max(1, len(title) // 2)
    checks.append((kw_front, "検索キーワードが前半にある"))
    score += 0.5 if kw_front else 0

    return min(10, round(score, 1)), checks


def suggest_tags(title):
    """タイトルから推奨タグを自動生成"""
    nouns = re.findall(r'[ァ-ヶー]{3,}|[一-龥]{2,}|[a-zA-Z]{3,}', title)
    seen = set(); base = []
    for n in nouns:
        if n not in seen and not re.match(r'^(方法|やり方|理由|結果|解説|完全|初心者|入門)$', n):
            seen.add(n); base.append(n)
        if len(base) >= 3: break
    if not base:
        return []
    tags = list(base)
    suffixes = ['使い方', 'やり方', '解説', '初心者', '入門', 'コツ', 'おすすめ']
    for s in suffixes[:5]:
        tags.append(f"{base[0]} {s}")
    if len(base) >= 2:
        tags.append(f"{base[0]} {base[1]}")
        tags.append(f"{base[1]} 使い方")
    return tags[:15]


# ════════════════════════════════════════════════════════
# コメント感情分析
# ════════════════════════════════════════════════════════

RE_POS = r'神|最高|すごい|凄い|面白|おもしろ|勉強にな|助かり|ありがと|分かりやすい|わかりやすい|好き|笑った|参考にな|感動|納得|嬉しい|期待|応援|楽しい|良かった|よかった'
RE_NEG = r'つまらな|微妙|分かりにく|わかりにく|長すぎ|うるさ|嫌い|残念|ひどい|最悪|意味不明|飽き|違う|嘘|うそ'

STOPWORDS = {'これ', 'それ', 'あれ', 'ここ', 'そこ', 'こと', 'もの', 'ため', 'よう', 'さん', 'です', 'ます', 'した', 'する', 'いる', 'ある', 'ない', 'なる', 'れる', 'られ', 'って', 'けど', 'から', 'まで', 'など', 'とき', '動画', '自分', '本当', '今回'}

def analyze_comments(comments):
    pos, neg, neu = 0, 0, 0
    words = Counter()
    for c in comments:
        t = c['text']
        p = bool(re.search(RE_POS, t))
        n = bool(re.search(RE_NEG, t))
        if p and not n: pos += 1
        elif n and not p: neg += 1
        else: neu += 1
        for w in re.findall(r'[ァ-ヶー]{2,}|[一-龥]{2,}', t):
            if w not in STOPWORDS:
                words[w] += 1
    total = len(comments)
    return {
        'pos': pos, 'neg': neg, 'neu': neu, 'total': total,
        'pos_rate': pos / total * 100 if total else 0,
        'neg_rate': neg / total * 100 if total else 0,
        'top_words': words.most_common(10),
        'top_comments': sorted(comments, key=lambda c: -c['likes'])[:5],
    }


# ════════════════════════════════════════════════════════
# 30日予測・レポート
# ════════════════════════════════════════════════════════

def project_views(views, vpd, days):
    """簡易的な30日後再生数予測（経過日数による減衰込み）"""
    if days < 7:    decay = 1.1
    elif days < 30: decay = 0.9
    elif days < 90: decay = 0.7
    else:           decay = 0.4
    return int(views + vpd * 30 * decay)


def build_report_md(v, result):
    g, _ = grade_of(result['total'])
    lines = [
        "# YouTube動画分析レポート",
        "",
        f"**動画：** {v['title']}",
        f"**チャンネル：** {v['channel_title']}（登録者{v['subscribers']:,}人）",
        f"**URL：** https://www.youtube.com/watch?v={v['id']}",
        f"**分析日：** {datetime.now().strftime('%Y-%m-%d')}",
        "",
        f"## 総合スコア：{result['total']}点（グレード{g}）",
        f"- 実績スコア：{result['perf_score']}点",
        f"- パッケージスコア：{result['pack_score']}点",
        f"- 再生数：{v['views']:,}回 / いいね率：{result['like_rate']:.1f}% / コメント率：{result['comment_rate']:.2f}%",
        "",
        "## 項目別スコア",
    ]
    for k, s in result['scores'].items():
        lines.append(f"- {k}: {s}/10")
    if result['critical']:
        lines.append("\n## 🚨 緊急で直すべき問題")
        for c in result['critical']:
            lines.append(f"- {c}")
    if result['good']:
        lines.append("\n## ✅ 良い点")
        for x in result['good']:
            lines.append(f"- {x}")
    if result['bad']:
        lines.append("\n## ⚠️ 改善できる点")
        for x in result['bad']:
            lines.append(f"- {x}")
    if result['impr']:
        lines.append("\n## 💡 改善アクション（優先順）")
        for i, x in enumerate(result['impr'], 1):
            lines.append(f"{i}. {x}")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════
# 共通UI：分析結果の表示
# ════════════════════════════════════════════════════════

def render_analysis(v, result):
    bench = result.get('bench')
    c1, c2 = st.columns([1, 2])
    with c1:
        if v['thumbnail_url']:
            st.image(v['thumbnail_url'], use_container_width=True)
    with c2:
        st.markdown(f"### {v['title']}")
        st.caption(f"{v['channel_title']} ・ 登録者{v['subscribers']:,}人 ・ 動画総数{v.get('total_channel_videos',0)}本")
        total = result['total']
        g, g_emoji = grade_of(total)
        color = "🟢" if total >= 70 else "🟡" if total >= 50 else "🔴"
        st.markdown(f"## {color} 総合スコア: **{total}点** / 100点　{g_emoji} グレード **{g}**")

        if result.get('buzz_label'):
            st.markdown(f'<div class="buzz-banner">{result["buzz_label"]}</div>', unsafe_allow_html=True)

        s1, s2 = st.columns(2)
        s1.metric("🔥 実績スコア", f"{result['perf_score']}点", help="再生数・エンゲージメント・視聴ペースなど実際の結果")
        s2.metric("📦 パッケージスコア", f"{result['pack_score']}点", help="タイトル・サムネイル・概要欄などの作り込み（伸びしろ）")

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("再生数", f"{v['views']:,}")
        m2.metric("日次再生", f"{result['views_per_day']:.0f}回/日")
        m3.metric("いいね率", f"{result['like_rate']:.1f}%")
        m4.metric("コメント率", f"{result['comment_rate']:.2f}%")
        m5.metric("投稿", f"{v['days_since']}日前")

        proj = project_views(v['views'], result['views_per_day'], v['days_since'])
        st.caption(f"📈 30日後の予測再生数：約 {proj:,} 回（現在のペースから簡易予測）")

        if bench:
            st.caption(f"📊 チャンネル中央値（直近{bench['sample']}本）: "
                       f"再生{bench['avg_views']:,.0f} / 日次{bench['avg_vpd']:.0f}回 / "
                       f"いいね率{bench['avg_like_rate']:.1f}%　※外れ値に強い中央値で比較")

    st.divider()

    st.subheader("📊 項目別スコア")
    for key, score in result['scores'].items():
        label = f"🔥 {key}" if key == '実績' else key
        if score <= 3:
            st.error(f"🚨 {label}: {score}/10")
        elif score <= 5:
            st.warning(f"⚠️ {label}: {score}/10")
        else:
            st.success(f"✅ {label}: {score}/10")
        st.progress(min(1.0, score / 10))

    st.divider()

    if result['critical']:
        st.subheader("🚨 緊急で直すべき問題")
        for c in result['critical']:
            st.markdown(f'<div class="critical-box">❌ {c}</div>', unsafe_allow_html=True)

    if result['good']:
        st.subheader("✅ 良い点")
        cols = st.columns(2)
        for i, g_item in enumerate(result['good']):
            cols[i%2].markdown(f'<span class="tag-good">{g_item}</span>', unsafe_allow_html=True)

    if result['bad']:
        label = "📈 さらに伸ばす余地" if result.get('is_proven') else "⚠️ 問題点"
        st.subheader(label)
        cols = st.columns(2)
        for i, b in enumerate(result['bad']):
            cols[i%2].markdown(f'<span class="tag-bad">{b}</span>', unsafe_allow_html=True)

    if result['impr']:
        st.subheader("💡 改善アクション（優先順）")
        for i, imp in enumerate(result['impr'], 1):
            st.markdown(f'<div class="impr-box"><strong>#{i}</strong>　{imp}</div>', unsafe_allow_html=True)

    st.divider()

    if result.get('patterns'):
        st.subheader("🔍 バズる動画の共通パターン チェック")
        patterns = result['patterns']
        met_count = sum(1 for p in patterns if p['met'] is True)
        total_cnt = sum(1 for p in patterns if p['met'] is not None)
        pct       = met_count / total_cnt * 100 if total_cnt > 0 else 0
        color_emoji = "🟢" if pct >= 75 else "🟡" if pct >= 50 else "🔴"
        st.markdown(f"{color_emoji} **達成 {met_count}/{total_cnt} 項目（{pct:.0f}%）** ← 型チェック。すでにバズっている動画は型より中身で勝っている場合もある")
        st.progress(pct / 100)
        st.write("")

        cols = st.columns(2)
        for i, p in enumerate(patterns):
            col = cols[i % 2]
            met = p['met']
            if met is None:
                col.markdown(
                    f"<div style='padding:6px 0; color:#888; font-size:13px;'>⬜ {p['label']}<br>"
                    f"<span style='font-size:11px; margin-left:20px;'>{p['detail']}</span></div>",
                    unsafe_allow_html=True)
            elif met:
                col.markdown(
                    f"<div style='padding:6px 0; font-size:13px;'>✅ <strong>{p['label']}</strong><br>"
                    f"<span style='font-size:11px; color:#16a34a; margin-left:20px;'>{p['detail']}</span></div>",
                    unsafe_allow_html=True)
            else:
                fix_html = ''
                if p.get('fix'):
                    fix_html = (
                        f"<details style='margin-top:4px;'>"
                        f"<summary style='font-size:11px; color:#2563eb; cursor:pointer; margin-left:20px;'>→ 直し方を見る</summary>"
                        f"<div style='font-size:11px; color:#1f2937; background:#eff6ff; border-left:3px solid #2563eb; padding:6px 10px; margin:4px 0 4px 20px; border-radius:0 6px 6px 0; line-height:1.7;'>{p['fix']}</div>"
                        f"</details>"
                    )
                col.markdown(
                    f"<div style='padding:6px 0; font-size:13px;'>❌ <strong>{p['label']}</strong><br>"
                    f"<span style='font-size:11px; color:#dc2626; margin-left:20px;'>{p['detail']} ── {p['why']}</span>"
                    f"{fix_html}</div>",
                    unsafe_allow_html=True)


# ════════════════════════════════════════════════════════
# サイドバー
# ════════════════════════════════════════════════════════

with st.sidebar:
    st.title("🎬 YouTube 動画分析")
    st.caption("v3.0 — フル機能版")
    st.markdown("""
**スコアの考え方**
- 🔥 実際に伸びた動画は高評価（結果が正義）
- 📦 パッケージは伸びしろとして提示
- 📊 チャンネル中央値・登録者規模を考慮

**機能**
- 📊 動画分析（採点＋改善アクション）
- ⚔️ 2本比較（自分 vs バズ動画）
- 📺 チャンネル分析（勝ちパターン抽出）
- 💬 コメント感情分析
- ✍️ タイトルシミュレーター
- 🏷️ タグ自動提案
- 📄 レポートDL
""")
    quota = st.session_state.get('quota_used', 0)
    st.divider()
    st.caption(f"⚡ このセッションのAPI使用量：約{quota:,} / 10,000ユニット")
    st.caption("※同じURLの再分析は1時間キャッシュされノーコスト")


# ════════════════════════════════════════════════════════
# メイン：3つのタブ
# ════════════════════════════════════════════════════════

tab1, tab2, tab3 = st.tabs(["📊 動画分析", "⚔️ 2本比較", "📺 チャンネル分析"])

# ────────────────────────────────────────────────────────
# タブ1：動画分析
# ────────────────────────────────────────────────────────
with tab1:
    st.title("📊 動画分析")
    st.caption("URLを貼ると動画を実績×パッケージの2軸で採点します。")

    url = st.text_input("YouTube動画のURLを入力", placeholder="https://www.youtube.com/watch?v=...",
                        key="analysis_url")

    if url:
        vid_id = extract_video_id(url)
        if not vid_id:
            st.error("URLからビデオIDを取得できませんでした。")
            st.stop()

        with st.spinner("動画データ取得中..."):
            try:
                v = fetch_video(vid_id)
                if not v:
                    st.error("動画が見つかりません。")
                    st.stop()
            except urllib.error.HTTPError as e:
                if e.code == 403:
                    st.error("APIクォータの上限に達した可能性があります。明日また試してください。")
                else:
                    st.error(f"APIエラー: {e}")
                st.stop()
            except Exception as e:
                st.error(f"エラー: {e}"); st.stop()
        with st.spinner("チャンネルベンチマーク取得中..."):
            bench = fetch_channel_benchmark(v['channel_id'], vid_id)
        result = analyze(v, bench)

        render_analysis(v, result)

        st.divider()

        # ─── レポートダウンロード ───
        report_md = build_report_md(v, result)
        st.download_button("📄 分析レポートをダウンロード（Markdown）", report_md,
                           file_name=f"yt_report_{vid_id}.md", mime="text/markdown")

        # ─── コメント感情分析 ───
        with st.expander("💬 コメント感情分析（クリックで開く）"):
            if st.button("コメントを分析する", key="cmt_btn"):
                with st.spinner("コメント取得中..."):
                    comments = fetch_comments(vid_id)
                if comments is None:
                    st.info("コメントが無効化されているか取得できませんでした。")
                elif not comments:
                    st.info("コメントがまだありません。")
                else:
                    ca = analyze_comments(comments)
                    cc1, cc2, cc3 = st.columns(3)
                    cc1.metric("😊 ポジティブ", f"{ca['pos']}件（{ca['pos_rate']:.0f}%）")
                    cc2.metric("😞 ネガティブ", f"{ca['neg']}件（{ca['neg_rate']:.0f}%）")
                    cc3.metric("😐 中立", f"{ca['neu']}件")
                    if ca['pos_rate'] >= 30 and ca['neg_rate'] <= 5:
                        st.success("視聴者の反応は非常にポジティブ。このテーマ・スタイルは継続すべき")
                    elif ca['neg_rate'] >= 20:
                        st.error("ネガティブ反応が多め。コメントを読んで原因（内容・音質・テンポ等）を特定すべき")
                    if ca['top_words']:
                        st.markdown("**頻出ワード：** " + " / ".join(f"{w}({c})" for w, c in ca['top_words']))
                    st.markdown("**👍 最も支持されたコメント：**")
                    for c in ca['top_comments']:
                        st.markdown(f"> {c['text'][:120]}{'…' if len(c['text'])>120 else ''}　`👍{c['likes']}`")

        # ─── タイトルシミュレーター ───
        with st.expander("✍️ タイトルシミュレーター（書き直し案を即採点）"):
            cur_score, cur_checks = evaluate_title_draft(v['title'])
            st.markdown(f"**現在のタイトル：** {v['title']}　→　**{cur_score}/10点**")
            draft = st.text_input("新しいタイトル案を入力", key="title_draft",
                                  placeholder="例：【初心者向け】〇〇を5分で自動化する3つの方法")
            if draft:
                new_score, new_checks = evaluate_title_draft(draft)
                diff = new_score - cur_score
                emoji = "🟢" if diff > 0 else ("🔴" if diff < 0 else "⚪")
                st.markdown(f"### {emoji} 新案：**{new_score}/10点**（現在から{diff:+.1f}点）")
                for ok, label in new_checks:
                    st.markdown(f"{'✅' if ok else '❌'} {label}")

        # ─── タグ自動提案 ───
        with st.expander("🏷️ タグ自動提案（コピペで使える）"):
            sugg = suggest_tags(v['title'])
            if sugg:
                st.markdown("タイトルから生成した推奨タグ（YouTube Studio → 詳細タブ → タグ欄にコピペ）：")
                st.code(", ".join(sugg))
            else:
                st.info("タイトルからキーワードを抽出できませんでした。")

# ────────────────────────────────────────────────────────
# タブ2：2本比較
# ────────────────────────────────────────────────────────
with tab2:
    st.title("⚔️ 2本比較")
    st.caption("自分の動画とバズ動画を並べて、何が違うのかを比較します。")

    cu1, cu2 = st.columns(2)
    url_a = cu1.text_input("動画A（自分の動画など）", key="cmp_a", placeholder="https://www.youtube.com/watch?v=...")
    url_b = cu2.text_input("動画B（バズ動画など）", key="cmp_b", placeholder="https://www.youtube.com/watch?v=...")

    if st.button("⚔️ 比較する", key="cmp_btn") and url_a and url_b:
        ida, idb = extract_video_id(url_a), extract_video_id(url_b)
        if not ida or not idb:
            st.error("URLからビデオIDを取得できませんでした。")
        else:
            with st.spinner("2本の動画を分析中..."):
                try:
                    va = fetch_video(ida); vb = fetch_video(idb)
                    if not va or not vb:
                        st.error("動画が見つかりません。"); st.stop()
                    ba = fetch_channel_benchmark(va['channel_id'], ida)
                    bb = fetch_channel_benchmark(vb['channel_id'], idb)
                    ra = analyze(va, ba); rb = analyze(vb, bb)
                except urllib.error.HTTPError:
                    st.error("APIエラー（クォータ上限の可能性）"); st.stop()

            ca, cb = st.columns(2)
            for col, v_, r_, tag_ in [(ca, va, ra, "🅰️"), (cb, vb, rb, "🅱️")]:
                with col:
                    if v_['thumbnail_url']: st.image(v_['thumbnail_url'], use_container_width=True)
                    g_, ge_ = grade_of(r_['total'])
                    st.markdown(f"{tag_} **{v_['title'][:40]}**")
                    st.caption(f"{v_['channel_title']}・登録者{v_['subscribers']:,}人")
                    st.markdown(f"## {ge_} {r_['total']}点（{g_}）")
                    st.metric("再生数", f"{v_['views']:,}")

            st.divider()
            st.subheader("📊 項目別の勝敗")
            rows = []
            wins_a = wins_b = 0
            metric_pairs = [
                ('総合スコア', ra['total'], rb['total'], '点'),
                ('実績スコア', ra['perf_score'], rb['perf_score'], '点'),
                ('パッケージスコア', ra['pack_score'], rb['pack_score'], '点'),
                ('再生数', va['views'], vb['views'], '回'),
                ('日次再生', round(ra['views_per_day']), round(rb['views_per_day']), '回/日'),
                ('いいね率', round(ra['like_rate'], 1), round(rb['like_rate'], 1), '%'),
                ('コメント率', round(ra['comment_rate'], 2), round(rb['comment_rate'], 2), '%'),
            ]
            for name, a_val, b_val, unit in metric_pairs:
                if a_val > b_val: w = "🅰️"; wins_a += 1
                elif b_val > a_val: w = "🅱️"; wins_b += 1
                else: w = "🤝"
                rows.append({'項目': name, '動画A': f"{a_val:,}{unit}", '動画B': f"{b_val:,}{unit}", '勝者': w})
            for k in ra['scores']:
                a_s, b_s = ra['scores'][k], rb['scores'].get(k, 0)
                if a_s > b_s: w = "🅰️"; wins_a += 1
                elif b_s > a_s: w = "🅱️"; wins_b += 1
                else: w = "🤝"
                rows.append({'項目': k, '動画A': f"{a_s}/10", '動画B': f"{b_s}/10", '勝者': w})
            st.dataframe(rows, use_container_width=True, hide_index=True)

            st.subheader("🏆 結論")
            if wins_a > wins_b:
                st.markdown(f'<div class="win-box">動画Aの勝ち（{wins_a}勝{wins_b}敗）。動画Bが勝っている項目が動画Aの改善ヒント。</div>', unsafe_allow_html=True)
            elif wins_b > wins_a:
                st.markdown(f'<div class="win-box">動画Bの勝ち（{wins_b}勝{wins_a}敗）。動画Bが勝っている項目（特にパッケージ系）を自分の動画に取り入れるのが近道。</div>', unsafe_allow_html=True)
            else:
                st.markdown(f'<div class="win-box">引き分け（{wins_a}勝{wins_b}敗）。項目別の差分を見て弱点を補強しよう。</div>', unsafe_allow_html=True)

            # ── 負けた動画の詳細改善プラン ──
            if (ra['total'], wins_a) >= (rb['total'], wins_b):
                win_v, win_r, win_tag = va, ra, "🅰️ 動画A"
                lose_v, lose_r, lose_tag = vb, rb, "🅱️ 動画B"
            else:
                win_v, win_r, win_tag = vb, rb, "🅱️ 動画B"
                lose_v, lose_r, lose_tag = va, ra, "🅰️ 動画A"

            st.divider()
            st.subheader(f"🛠️ {lose_tag} の改善プラン（{win_tag} に追いつくには）")
            st.caption(f"対象：{lose_v['title'][:50]}")

            # ① 差が大きいカテゴリから順に提示
            gaps = []
            for k in lose_r['scores']:
                diff = win_r['scores'].get(k, 0) - lose_r['scores'][k]
                if diff > 0:
                    gaps.append((k, lose_r['scores'][k], win_r['scores'].get(k, 0), diff))
            gaps.sort(key=lambda x: -x[3])

            if gaps:
                st.markdown("**📊 差がついているカテゴリ（差が大きい順）── ここを直すのが最短ルート：**")
                gap_rows = [{'カテゴリ': k, lose_tag: f"{ls}/10", win_tag: f"{ws}/10", '差': f"-{diff}点"}
                            for k, ls, ws, diff in gaps]
                st.dataframe(gap_rows, use_container_width=True, hide_index=True)
            else:
                st.info("カテゴリ別では負けている項目がありません。実績（再生数）の差が主な要因です。")

            # ② 最優先で直すこと
            if lose_r['critical']:
                st.markdown("**🚨 最優先で直すこと：**")
                for c in lose_r['critical']:
                    st.markdown(f'<div class="critical-box">❌ {c}</div>', unsafe_allow_html=True)

            # ③ 具体的な改善手順（YouTube Studioの操作場所付き）
            if lose_r['impr']:
                st.markdown("**💡 具体的な改善手順（優先順・YouTube Studioの操作場所付き）：**")
                for i, imp in enumerate(lose_r['impr'], 1):
                    st.markdown(f'<div class="impr-box"><strong>#{i}</strong>　{imp}</div>', unsafe_allow_html=True)

            # ④ 負けた動画のパターンチェックで❌だった項目の直し方
            lose_fails = [p for p in lose_r.get('patterns', []) if p['met'] is False and p.get('fix')]
            if lose_fails:
                st.markdown("**🔍 バズるパターンで未達成の項目と直し方：**")
                for p in lose_fails:
                    with st.expander(f"❌ {p['label']} ── {p['why']}"):
                        st.markdown(p['fix'])

            # ⑤ 勝った動画から盗めるポイント
            copyable = win_r['good'][:8]
            if copyable:
                st.markdown(f"**🏆 {win_tag} から盗めるポイント（勝者がやれていること）：**")
                for g_item in copyable:
                    st.markdown(f'<div class="win-box">✅ {g_item}</div>', unsafe_allow_html=True)

# ────────────────────────────────────────────────────────
# タブ3：チャンネル分析
# ────────────────────────────────────────────────────────
with tab3:
    st.title("📺 チャンネル分析")
    st.caption("チャンネルの直近動画を一括分析し、勝ちパターンを抽出します。")

    ch_input = st.text_input("チャンネルURL・@ハンドル・動画URLのどれでもOK", key="ch_input",
                             placeholder="https://www.youtube.com/@チャンネル名")

    if st.button("📺 チャンネルを分析する", key="ch_btn") and ch_input:
        with st.spinner("チャンネル情報取得中..."):
            ch = resolve_channel(ch_input)
        if not ch:
            st.error("チャンネルが見つかりませんでした。")
        else:
            with st.spinner("直近動画を取得中..."):
                vids = fetch_channel_videos(ch['id'])
            if not vids:
                st.error("動画が取得できませんでした。")
            else:
                st.markdown(f"### {ch['title']}")
                cm1, cm2, cm3, cm4 = st.columns(4)
                cm1.metric("登録者", f"{ch['subscribers']:,}人")
                cm2.metric("総動画数", f"{ch['total_videos']:,}本")
                cm3.metric("総再生数", f"{ch['total_views']:,}回")
                med_views = _median([x['views'] for x in vids])
                cm4.metric("直近の中央値", f"{med_views:,.0f}回")

                st.divider()

                # ── 動画テーブル ──
                st.subheader(f"📋 直近{len(vids)}本の成績表")
                table = []
                for x in sorted(vids, key=lambda a: -a['views']):
                    rel = x['views'] / med_views if med_views > 0 else 0
                    fire = "🔥" if rel >= 2 else ("✅" if rel >= 1 else "▫️")
                    table.append({
                        '': fire,
                        'タイトル': x['title'][:38],
                        '再生数': f"{x['views']:,}",
                        '中央値比': f"{rel:.1f}倍",
                        'いいね率': f"{x['lr']:.1f}%",
                        '長さ': f"{x['duration']//60}分",
                        '投稿': x['published'],
                    })
                st.dataframe(table, use_container_width=True, hide_index=True)

                # ── 勝ちパターン抽出 ──
                if len(vids) >= 6:
                    st.subheader("🏆 勝ちパターン分析(上位3本 vs 下位3本)")
                    ranked = sorted(vids, key=lambda a: -a['views'])
                    top3, bottom3 = ranked[:3], ranked[-3:]

                    def traits(group):
                        return {
                            'len': sum(len(x['title']) for x in group) / len(group),
                            'num': sum(1 for x in group if re.search(r'\d', x['title'])) / len(group) * 100,
                            'emo': sum(1 for x in group if emotional_count_of(x['title']) >= 1) / len(group) * 100,
                            'dur': _median([x['duration'] for x in group]) / 60,
                            'lr': _median([x['lr'] for x in group]),
                        }
                    tt, tb = traits(top3), traits(bottom3)

                    comp = [
                        {'特徴': 'タイトル文字数', '🔥上位3本': f"{tt['len']:.0f}文字", '▫️下位3本': f"{tb['len']:.0f}文字"},
                        {'特徴': '数字入りタイトル率', '🔥上位3本': f"{tt['num']:.0f}%", '▫️下位3本': f"{tb['num']:.0f}%"},
                        {'特徴': '感情トリガー使用率', '🔥上位3本': f"{tt['emo']:.0f}%", '▫️下位3本': f"{tb['emo']:.0f}%"},
                        {'特徴': '動画の長さ（中央値）', '🔥上位3本': f"{tt['dur']:.0f}分", '▫️下位3本': f"{tb['dur']:.0f}分"},
                        {'特徴': 'いいね率（中央値）', '🔥上位3本': f"{tt['lr']:.1f}%", '▫️下位3本': f"{tb['lr']:.1f}%"},
                    ]
                    st.dataframe(comp, use_container_width=True, hide_index=True)

                    insights = []
                    if tt['num'] > tb['num'] + 20:
                        insights.append("数字入りタイトルの動画が伸びている → 次の動画も必ず数字を入れる")
                    if tt['emo'] > tb['emo'] + 20:
                        insights.append("感情トリガー入りタイトルが伸びている → 驚き・恐怖・欲求・共感ワードを使い続ける")
                    if abs(tt['dur'] - tb['dur']) >= 3:
                        direction = "長め" if tt['dur'] > tb['dur'] else "短め"
                        insights.append(f"伸びている動画は{direction}（約{tt['dur']:.0f}分）→ この長さに寄せる")
                    if tt['len'] > tb['len'] + 8:
                        insights.append(f"伸びている動画はタイトルが長め（約{tt['len']:.0f}文字）→ 情報量を増やす")
                    elif tb['len'] > tt['len'] + 8:
                        insights.append(f"伸びている動画はタイトルが短め（約{tt['len']:.0f}文字）→ 簡潔で強い言葉に絞る")
                    hours = Counter(x['hour_jst'] for x in top3)
                    best_hour = hours.most_common(1)[0][0] if hours else None
                    if best_hour is not None:
                        insights.append(f"上位動画に多い投稿時間はJST {best_hour}時台 → 次回も同じ時間帯を狙う")

                    if insights:
                        st.markdown("**📌 このチャンネルの勝ちパターン：**")
                        for ins in insights:
                            st.markdown(f'<div class="win-box">💡 {ins}</div>', unsafe_allow_html=True)
                    else:
                        st.info("上位と下位で明確なパターン差は見つかりませんでした。テーマ自体の差が大きい可能性があります。")
                else:
                    st.info("動画本数が少ないため勝ちパターン分析はスキップしました（6本以上で有効）。")
