# Tamatama-swapper

SHIGEZONE「タマタマ ビデオプレーヤー701」（Crystal701 / Actions V100）の純正動画アプリのボタン割り当てを変えるツール。

| 動画再生中 | 左側の2ボタンを短押し | 左側の2ボタンを長押し |
| --- | --- | --- |
| 通常モード | 前後の動画へ | 音量を下げる／上げる。離すとバーを閉じる |
| シークモード | 前後の動画へ | 早戻し／早送り |

右端の調整ボタンを長押しするとモードが切り替わる。プレーヤーを開いたときは通常モード。

対応するのは、メーカー領域の物理ブロック3910・3911が既知の純正データと一致する個体。パッチで変わるのは計5ページ。NANDの消去はブロック単位なので、書き込みが必要なブロックは各256ページを組み直す。`patches/keymode-v3.bin` は追加ハンドラーのバイナリで、書き込み用データは自分の本体のバックアップから生成する。[パッチバイナリ](patches/keymode-v3.bin)

## 準備

Python 3.10以上、GNU MIPS little-endianのGCCとbinutilsを使う。WindowsではツールをWindows側のPythonで実行し、クロスコンパイラはWSLに入れる。ADFUデバイスをWinUSBで認識させる。

```bash
sudo apt install gcc-mipsel-linux-gnu binutils-mipsel-linux-gnu
```

以下はこのリポジトリのルートから実行する。

```powershell
git clone https://github.com/BlackLikeaCat/actions-adfu-tool.git ../adfu-bootstrap
git -C ../adfu-bootstrap checkout --detach 91c8f4709c5d36f916baa2109900df6b95dc396f
python -m pip install -r requirements.txt
python tamatama_swapper.py build --output out/loaders
```

`build` はUSBに接続せず、RAMローダーをソースからビルドする。実機で使ったビルドは `mipsel-linux-gnu-gcc` 10.3.0。付属のパッチバイナリを使えば `prepare` にコンパイラは不要。Windowsで使うWSLは `wsl --status` に表示される既定のディストリビューション。ADFUツールを別の場所に置く場合はサブコマンドの前に `--adfu-checkout` を指定する。

## バックアップ・パッチ・書き込み

本体を完全に電源オフにし、ADFUボタンを押しながらUSBにつなぐ。ADFU機器は1台にする。`backup` は各ブロックの全ページを生データとECC処理済みの両方で取得する。他の場所にも `out/backup` を保存しておく。

```powershell
python tamatama_swapper.py backup --loaders out/loaders --output out/backup
python tamatama_swapper.py prepare --backup out/backup --output out/patch
python tamatama_swapper.py --resume-from out/backup/backup.json dry-run --loaders out/loaders --backup out/backup --payloads out/patch --output out/dry-run
```

`dry-run` はCRC付きのRAM転送と、操作前後のNAND照合まで行う。`apply --write` が消去と書き込みを開始する。処理が完了するまで、電源とUSBはつないだままにする。各 `--output` には未作成のパスを使う。

```powershell
python tamatama_swapper.py --resume-from out/dry-run/operation.json apply --write --loaders out/loaders --backup out/backup --payloads out/patch --output out/apply
```

`apply` は保存データと実機を照合し、書き込みが必要なブロックだけを3911→3910の順に処理する。消去直後に読み戻し、各ページの書き込み直後には先頭1 KiBを最大3回読み戻す。最後に全ページを生データとハードウェアECCで独立に読み出す。ECC処理後の本体データとOOB管理情報、物理タグ、ECC保護外の予備領域を照合する。生データの差や再読の記録は、出力先の `operation.json` と読み戻しファイルに残る。

`operation.json` が `complete: true` になったら本体を通常起動し、動画再生中の左ボタンを長押しして確認する。後から書き込みなしで再照合する場合は、ADFUに入り直して `verify --expect patched` を使う。

```powershell
python tamatama_swapper.py verify --expect patched --loaders out/loaders --backup out/backup --payloads out/patch --output out/verify
```

## 純正へ戻す・中断から復旧する

本体をADFUでつなぎ、最初の `backup` を使う。

```powershell
python tamatama_swapper.py restore --write --loaders out/loaders --backup out/backup --output out/restore
```

書き込みが中断したら、元のバックアップと失敗した操作の `operation.json` を保持する。本体がADFUで応答する状態にし、同じUSBポートから `restore` または `apply` に `--recover-from <中断時のoperation.json>` を加えて再試行する。中断前に確認できたもう一方のブロックと照合してから復旧を進める。RAMローダーが同じ接続で応答している場合は `--resume-from` も使える。

## 検証と出典

- 開発時の実機でバックアップ、ドライラン、両ブロックの書き込みと読み戻し、書き込み中断からの続行、動画中の長押しによる連続した音量変更を確認した。
- テストは `python -m unittest` で実行する。パッチバイナリの再生成手順と各ソースの出典は [NOTICE](NOTICE) を参照。
- ハードウェア調査とADFUの先行記録。[GOROmanさんのCrystal701調査](https://gist.github.com/GOROman/934e9c522632b303065777bb154a4ef6) · [tamatama-extractor](https://github.com/Keitark/tamatama-extractor)
