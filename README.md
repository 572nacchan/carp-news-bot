# carp-news-bot

Googleニュースで「広島カープ」を検索したRSSから新しい記事を拾って、Blueskyに自動投稿するBot。GitHub Actionsで毎時実行する。

## 仕組み
- 毎時7分（UTC基準）にGitHub Actionsが`bot.py`を実行する
- 投稿済み・24時間より古い・タイトルが重複している記事は除外する
- 古い順に**1回あたり最大5件**まで投稿（`【カープニュース】タイトル（媒体名）`＋リンクカード）
- 投稿済みの記事は`posted.json`に記録し、Actionsが自動でコミットする（記録は7日で削除）
- **初回実行時は投稿しない**。その時点の記事を「投稿済み」として登録するだけ（過去記事を一気に流さないため）

設定（検索語・件数など）は`bot.py`冒頭の定数で変えられる。

## セットアップ
1. Bot用のBlueskyアカウントで **設定 → プライバシーとセキュリティ → アプリパスワード** からアプリパスワードを発行
2. GitHubで新しいリポジトリ（例: `carp-news-bot`）を作成し、このフォルダをpushする
3. リポジトリの **Settings → Secrets and variables → Actions → New repository secret** に次の2つを登録
   - `BSKY_HANDLE` … 例: `carp-news.bsky.social`
   - `BSKY_APP_PASSWORD` … 1で発行したアプリパスワード
4. **Actions → post carp news → Run workflow** で手動実行（初回は登録だけで投稿されない）
5. 次の実行から新しい記事が投稿される

## ローカルで確認
```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python bot.py --dry-run   # 投稿せずに表示のみ（posted.jsonも更新しない）
```

## 制限など
- Blueskyの投稿上限は1時間に1,666件・1日に11,666件、ログイン上限は1日300回なので、毎時の実行なら十分余裕がある
- リンク先はGoogleニュースのリダイレクトURLなので、リンクカードにサムネは付かない
- GitHub Actionsのcronは混雑時に数分〜十数分遅れることがある
- 60日間リポジトリに動きがないとスケジュール実行が止まるが、`posted.json`の自動コミットがあるので通常は止まらない
