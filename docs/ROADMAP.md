# Product Roadmap: Credit Portfolio Optimizer

**Goal:** From Standalone PoC to "Enterprise Optimization Platform" (concept)

English | [日本語](#japanese)

---

## Scope Assumptions

This roadmap outlines a technical plan for evolving this personal
proof-of-concept into a platform where an AI agent can operate the
mathematical model to support business decision-making.

- **This app is not intended for production use or customer delivery.**
  It exists for demo and self-learning purposes.
- The following are therefore intentionally out of scope:
  - Availability / throughput design for production operation
  - Large-scale data handling and production-grade security / compliance
  - Staged rollout strategy (feature flags, limited release, etc.)
- **If production use were ever considered**, availability, throughput,
  data governance, and an authentication foundation would need to be
  discussed separately, in addition to the above.

---

## Phase 1: Separation of Concerns & Code Quality (Refactoring)

Moving from a single tightly-coupled file to a codebase with clear
separation of responsibilities that can be unit tested.

* Key Tasks:
  * [x] Extract post-processing business rules (rounding, housewife/student
        cap, no-decrease rule for the Shopping segment) into `domain/rules.py`,
        separate from the UI and solver
  * [x] Add unit tests for the business rules (Ipopt-independent,
        runs fast with pandas/numpy only)
  * [x] Set up a CI pipeline with GitHub Actions
  * [ ] Extract the mathematical model construction logic (Pyomo part)
        into its own module
  * [ ] Externalize business rule thresholds (rounding menu, caps, etc.)
        into a config file (YAML/JSON)
* Objectives:
  * Pay down technical debt through refactoring
  * Establish a structure that enables parallel development by multiple people

## Phase 2: Decoupling & API-fication

Defining the boundary between frontend and backend, so the optimization
logic becomes an independent "optimization engine API" not tied to any UI.
Given the scope, this is split into three stages.

* Key Tasks:
  * **2a-pre**: Set up a Dockerfile to pin the runtime environment (OS,
    Python version, system-level dependencies such as Ipopt) as a
    container image. This addresses environment drift discovered while
    deploying to multiple platforms (see the Ipopt troubleshooting note
    in the README): the same `get_ipopt_path()` fallback logic had to
    special-case each hosting environment (Streamlit Cloud's Conda path,
    Azure's amplpy-installed path, etc.), which does not scale as more
    environments are added.
    **Caveat**: Docker mitigates *application-layer* environment drift,
    but does not eliminate platform-layer differences — host CPU
    architecture (e.g. ARM64 vs x86_64), each cloud's container runtime
    constraints (port conventions, startup time limits, filesystem
    restrictions), and secret-management mechanisms still differ across
    providers and need separate handling.
  * **2a**: FastAPI-fication only. The existing Streamlit UI is changed to
    call the API, and the API contract (Pydantic schemas) is finalized
    (Strangler Fig pattern)
  * **2b**: Introduce an async task queue (Celery / Redis, etc.) and a job
    status API. The design accounts for the cost characteristics of Ipopt
    calls (`get_efficient_frontiers` alone makes 60 solver calls:
    3 scenarios × 20 points)
  * **2c**: Build a new frontend with React or similar
* Objectives:
  * Fully separate the UI (Streamlit) from computation (Pyomo/Ipopt)
  * Establish an interface that agents or other systems can call directly
    (Contract-first Design)
  * Enable independent, container-level scalability
* Deployment Strategy:
  * Streamlit Community Cloud cannot host a separate FastAPI process, a
    Celery/Redis worker, or a non-Streamlit frontend — it only runs a
    single Streamlit app. Phase 2 work therefore targets Azure Web App
    (Docker-based) as the primary deployment target from 2a-pre onward.
  * The existing Streamlit demo (`app.py`) is kept alive rather than
    retired: it still has value as a lightweight, shareable demo.
  * Rather than splitting into separate branches (which would require
    duplicating fixes to shared logic across branches), both
    deployments are built from a single `main` branch, separated by
    directory: `app.py` (+ `domain/`) stays the Streamlit Cloud entry
    point, while new FastAPI code lands under its own directory (e.g.
    `api/`) that Azure's Docker build targets. `domain/` remains the
    shared core consumed by both.
  * The Streamlit Cloud deployment URL may change; that is acceptable.

## Phase 3: Evolution into an Agent Platform (AI Agent Integration)

Enabling an LLM agent to use the optimization engine as a "tool,"
allowing advanced simulation via natural language.

* Key Tasks:
  * Prepare the API schema for Function Calling / Tool Use
    (strict Pydantic models)
  * Evaluate exposing the engine as an MCP (Model Context Protocol) server,
    rather than designing a custom Function Calling schema from scratch
  * Implement a session management layer for context retention
  * Introduce a natural-language (Chat) UI
  * **Human-in-the-loop approval flow**: require human approval before
    any credit limit change proposed by the agent is actually applied
* Objectives:
  * Execute autonomous optimization simulations in response to natural
    language instructions
  * Let the LLM infer and apply dynamic parameters, e.g. "if PD doubles
    under a severely pessimistic scenario"
  * Implement an agent that can explain the reasoning behind a decision
    in natural language

## Phase 4: Execution History & Traceability (Auditability & Persistence)

Persisting execution history so that the autonomous simulations run by
the agent in Phase 3 can be reviewed, verified, and explained afterward.
This also serves as the infrastructure underpinning Phase 3's
"agent that can explain its reasoning."

* Key Tasks:
  * Introduce a lightweight persistence layer using SQLite (file-based).
    No DB server is set up or operated; everything is self-contained
    in a single file
  * Design an execution history table: input parameters, output summary,
    timestamp, and actor (UI operation vs. agent-initiated)
  * Move customer data (currently randomly generated on each run) into
    the DB, enabling comparison of multiple scenarios / agent instructions
    against the same portfolio
  * Standardize access through an ORM (e.g. SQLAlchemy), keeping a
    migration path open to a production DB (PostgreSQL, etc.) later
* Objectives:
  * Enable the agent to explain "why this credit limit" by referencing
    past execution history
  * Establish a reproducible demo/verification environment that doesn't
    depend on randomly generated data each run
  * Make the agent's tool calls traceable

---

<a name="japanese"></a>
# (日本語)

[English](#product-roadmap-credit-portfolio-optimizer)

**ゴール:** スタンドアロンPoCから「エンタープライズ・オプティマイゼーション・プラットフォーム」（構想）へ

---

## スコープに関する前提

本ロードマップは、個人の自己研鑽目的で開発しているプロトタイプを、
「AIエージェントが数理モデルを操作し、経営意思決定を支援するプラットフォーム」
へと進化させる場合の技術的な計画である。

- **本アプリは顧客提供や本番運用を前提としていない。** あくまでデモ・学習用途。
- そのため、以下は意図的にスコープ外としている：
  - 本番運用を前提とした可用性・スループット設計
  - 大規模データ・本番相当のセキュリティ / コンプライアンス体制
  - 段階的ロールアウト戦略（フィーチャーフラグ、限定公開等）
- **仮に本番運用を将来検討する場合は、上記に加え、可用性・スループット・
  データガバナンス・認証基盤について別途議論が必要**と認識している。

---

## Phase 1: 責務の分離とコードベースの品質担保 (Refactoring)

単一ファイルの密結合アーキテクチャから、責務を分離し、自動テスト可能な
クリーンなコードベースへの移行。

* Key Tasks:
  * [x] ビジネスルール（丸め処理・主婦学生上限・Shopping減枠禁止）を
        `domain/rules.py` として UI / ソルバーから分離
  * [x] ビジネスルールに対するユニットテストの導入
        （Ipopt非依存、pandas/numpyのみで完結する軽量テスト）
  * [x] GitHub Actions による CI パイプラインの構築
  * [ ] 数理モデル構築ロジック（Pyomo部分）の分離
  * [ ] ビジネスルールの閾値（丸めメニュー、上限額等）を
        外部設定ファイル（YAML/JSON）に切り出し
* Objectives:
  * リファクタリングによる技術的負債の返済
  * 複数人での並行開発を可能にする構成の確立

## Phase 2: アーキテクチャの疎結合化とAPI提供 (Decoupling & API-fication)

フロントエンドとバックエンドの境界を定義し、UIに依存しない
「最適化エンジンAPI」としての独立。大規模変更のため、以下3段階に分割する。

* Key Tasks:
  * **2a-pre**: Dockerfileの整備。実行環境（OS・Pythonバージョン・
    Ipoptなどのシステム依存パッケージ）をコンテナイメージとして固定化する。
    複数環境へのデプロイで発覚した環境差異（README記載のIpopt
    トラブルシューティング参照）への対応：`get_ipopt_path()`のフォール
    バックロジックが、Streamlit CloudのCondaパス・Azureのamplpy
    インストールパスなど、環境ごとに個別対応を積み増す構造になって
    おり、環境が増えるたびにスケールしない設計だった。
    **留意点**：Dockerが吸収できるのは*アプリケーションレイヤー*の
    環境差異であり、プラットフォームレイヤーの差異（ホストのCPU
    アーキテクチャの違い〈ARM64 vs x86_64等〉、各クラウドのコンテナ
    実行基盤固有の制約〈ポート規約・起動時間制限・ファイルシステム
    制約〉、シークレット管理の仕組みの違い等）までは解消しない点に
    留意する。
  * **2a**: FastAPI化のみ。既存Streamlit UIはAPIを叩く構成に変更し、
    API契約（Pydanticスキーマ）を固める（Strangler Figパターン）
  * **2b**: 非同期タスクキュー（Celery / Redis等）とジョブステータスAPIの導入。
    Ipopt呼び出しのコスト特性（`get_efficient_frontiers` は
    3シナリオ×20点=60回のソルバー呼び出し）を踏まえた設計とする
  * **2c**: React等によるフロントエンドの新規構築
* Objectives:
  * UI（Streamlit）と計算処理（Pyomo/Ipopt）の完全分離
  * Agentや他システムが直接APIを叩けるインターフェースの確立
    （Contract-first Design）
  * コンテナ単位での独立したスケーラビリティの確保
* デプロイ方針:
  * Streamlit Community Cloudは単一のStreamlitアプリしか動かせず、
    別プロセスのFastAPIサーバー・Celery/Redisワーカー・
    Streamlit以外のフロントエンドは配置できない。そのため2a-pre以降、
    Azure Web App（Docker）を主デプロイ先として進める。
  * 既存のStreamlitデモ（`app.py`）は廃止せず残す。軽量に共有できる
    デモとして引き続き価値がある
  * ブランチを分岐すると共有ロジックの修正を両ブランチに反映する
    二重メンテが発生するため、分岐はせず`main`ブランチ1本のまま、
    ディレクトリで分離する：`app.py`（＋`domain/`）はStreamlit Cloud
    向けエントリーポイントとして維持し、新設するFastAPIコードは
    別ディレクトリ（例：`api/`）に置き、Azure側のDockerビルドは
    そちらを対象とする。`domain/`は両者が共有する中核として維持する
  * Streamlit CloudのデプロイURLが変わっても差し支えない

## Phase 3: エージェント・プラットフォームへの進化 (AI Agent Integration)

LLMエージェントが最適化エンジンを「ツール」として活用し、
自然言語での高度なシミュレーションを実現。

* Key Tasks:
  * Function Calling (Tool Use) のためのAPIスキーマ整備
    （Pydanticモデルの厳格化）
  * MCP（Model Context Protocol）サーバー化の検討。独自スキーマを
    ゼロから設計するより、標準化されたツール定義・認証・セッション管理を
    活用できないかを評価する
  * 文脈維持（Context Management）のためのセッション管理基盤の実装
  * 自然言語UI (Chat UI) の導入
  * **Human-in-the-Loopの承認フロー**：エージェントが提案した与信枠変更を
    実際に適用する前に、人間の承認を必須とするゲートを設ける
* Objectives:
  * ユーザーの自然言語指示に対する、自律的な最適化シミュレーションの実行
  * 「超悲観シナリオでデフォルト確率が2倍の場合」といった動的パラメータの
    LLMによる推論と反映
  * 意思決定の背景を自然言語で説明できるAgentの実装

## Phase 4: 実行履歴とトレーサビリティ (Auditability & Persistence)

Phase 3で実現したエージェントによる自律的なシミュレーション実行を、
後から振り返り・検証・説明できるようにする。Phase 3の「意思決定の背景を
説明できるAgent」を実質的に支えるインフラでもある。

* Key Tasks:
  * SQLite（ファイルベース）による軽量な永続化層の導入。
    DBサーバーの構築・運用は行わず、単一ファイルでの完結を前提とする
  * 実行履歴テーブルの設計：入力パラメータ・出力サマリ・実行日時・
    実行主体（UI操作 or Agent経由）を記録
  * ダミー生成に依存していた顧客データのDB化。同一ポートフォリオに対する
    複数シナリオ・複数エージェント指示の結果比較を可能にする
  * SQLAlchemy等のORM経由でのアクセスに統一し、将来的な本番DB
    （PostgreSQL等）への移行パスを確保する
* Objectives:
  * 「なぜこの与信枠になったか」をエージェントが過去の実行履歴を
    参照して説明できる状態を作る
  * 毎回ランダム生成されるデータに依存しない、再現性のあるデモ環境の確立
  * エージェントのツール呼び出しを追跡可能にする
