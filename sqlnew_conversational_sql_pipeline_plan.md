# SQLNEW Conversational One-LLM SQL Pipeline Plan

## 1. Goal

Build a new SQL pipeline inside `SQLNEW` that can handle both:

1. **New database questions**
2. **Back-and-forth follow-up questions** about previous SQL, previous results, refinements, and drill-downs

The pipeline should use only **one LLM call per user turn**.

The main idea:

```text
User message
↓
Conversation memory + embedding retrieval + compact skill context
↓
One LLM call
↓
SQL or memory-based answer
↓
Execute SQL if needed
↓
Store updated memory
```

The model should not receive the full database, full `skill.md`, or all rows. It should receive only a compact context relevant to the current turn.

---

## 2. Core Design Principle

```text
skill.md = full database knowledge
embedding index = searchable database meaning
conversation memory = what happened before
LLM skill context = compact retrieved context for this turn
LLM call = classify the turn + generate SQL or answer from memory
```

The app/backend must manage memory. The LLM should not be trusted to remember previous turns by itself.

---

## 3. Final High-Level Pipeline

```text
1. User sends message
   ↓
2. Load conversation memory
   ↓
3. Build conversational input
   ↓
4. Normalize and expand current message
   ↓
5. Decide retrieval mode using memory:
      - new query
      - refine previous query
      - ask about previous SQL
      - ask about previous result
      - drill down previous result
      - explain previous result
   ↓
6. Retrieve database skill context if SQL may be needed
   ↓
7. Build compact LLM skill context
   ↓
8. Send one prompt to LLM
   ↓
9. LLM returns structured JSON
   ↓
10. If needs_sql = true:
       validate SQL
       execute SQL
       summarize result
       update memory
   ↓
11. If needs_sql = false:
       answer from memory
       optionally update memory
```

---

## 4. Folder Structure

Recommended `SQLNEW` structure:

```text
SQLNEW/
├── app/
│   ├── main.py
│   └── api.py
│
├── config.py
│
├── skills/
│   └── sales/
│       ├── skill.md
│       ├── schema_snapshot.json
│       ├── embedding_docs.jsonl
│       ├── vector_index.faiss
│       └── metadata.json
│
├── ingestion/
│   ├── schema_loader.py
│   ├── skill_parser.py
│   ├── embedding_doc_builder.py
│   └── build_indexes.py
│
├── retrieval/
│   ├── normalizer.py
│   ├── query_expander.py
│   ├── vector_retriever.py
│   ├── exact_matcher.py
│   ├── table_resolver.py
│   ├── join_expander.py
│   └── context_builder.py
│
├── memory/
│   ├── conversation_store.py
│   ├── memory_builder.py
│   └── result_summarizer.py
│
├── llm/
│   ├── client.py
│   ├── prompt_builder.py
│   └── response_parser.py
│
├── validation/
│   ├── sql_validator.py
│   └── sql_safety.py
│
├── execution/
│   ├── db_client.py
│   └── query_runner.py
│
└── run.py
```

---

# Part A — Writing `skill.md`

## 5. Purpose of `skill.md`

`skill.md` is the full human-readable and machine-readable database knowledge file.

It should describe:

```text
1. Global SQL rules
2. Vietnamese normalization rules
3. Metric formulas
4. Table meanings
5. Column meanings
6. Allowed joins
7. Join paths
8. Important values/entities
9. When to use each table
10. When not to use each table
```

The LLM should not receive the entire `skill.md` every time.

Instead:

```text
skill.md
↓
parsed into embedding documents
↓
retrieved by semantic search
↓
compressed into compact LLM skill context
```

---

## 6. Recommended `skill.md` Format

```md
# Database Skill: FMCG Sales Database

## SQL Dialect
MySQL

## Global SQL Rules
- Use only listed tables.
- Use only listed columns.
- Use only listed joins.
- Do not invent tables.
- Do not invent columns.
- Do not invent join conditions.
- Return one executable SQL query when SQL is needed.
- Database identifiers use Vietnamese không dấu with snake_case.
- User questions may be written in Vietnamese có dấu.

## Vietnamese Normalization Rules
- công ty → cong_ty
- khách hàng → khach_hang
- nhà phân phối → nha_phan_phoi
- đơn hàng bán → don_hang_ban
- chi tiết đơn hàng bán → chi_tiet_don_hang_ban
- sản phẩm → san_pham
- khuyến mãi → khuyen_mai
- doanh thu → doanh_thu
- doanh số → doanh_thu

## Metric: doanh_thu
Aliases:
- doanh thu
- doanh số
- sales
- revenue
- tổng tiền bán hàng

Formula:
SUM(chi_tiet_don_hang_ban.so_luong * chi_tiet_don_hang_ban.don_gia)

Required tables:
- don_hang_ban
- chi_tiet_don_hang_ban

Required join:
don_hang_ban.don_hang_ban_id = chi_tiet_don_hang_ban.don_hang_ban_id

Use when:
The user asks about revenue, sales amount, total money, or sales value.

## Table: cong_ty

### Business meaning
Represents an FMCG company, supplier, or brand owner.

Vietnamese meaning:
Công ty FMCG hoặc chủ thương hiệu bán sản phẩm thông qua nhà phân phối.

### Use this table when
Use when the user asks about:
- công ty
- doanh nghiệp
- chủ thương hiệu
- brand owner
- company
- supplier company
- sản phẩm theo công ty
- doanh thu theo công ty

### Do not use this table alone when
Do not use this table alone for:
- doanh thu
- doanh số
- số lượng bán
- khách hàng mua nhiều nhất

For revenue questions, join to `don_hang_ban` and `chi_tiet_don_hang_ban`.

### Primary key
- cong_ty_id

### Columns
- cong_ty_id: company ID, primary key
- ten_cong_ty: company name
- nganh_hang: business category or industry

### Allowed joins
- cong_ty.cong_ty_id = don_hang_ban.cong_ty_id
- cong_ty.cong_ty_id = san_pham.cong_ty_id
- cong_ty.cong_ty_id = nha_phan_phoi.cong_ty_id
- cong_ty.cong_ty_id = khuyen_mai.cong_ty_id

### Common values
- cong_ty_id: CTY_001, CTY_002
- ten_cong_ty: Cong ty FMCG An Phat, Nuoc Giai Khat Sao Viet
- nganh_hang: FMCG, Beverage

### Retrieval text
cong_ty means company, brand owner, supplier company, doanh nghiep, cong ty, chu thuong hieu, FMCG company. It connects company to products, orders, promotions, and distributors through cong_ty_id.
```

---

# Part B — Embedding Documents

## 7. What to Embed

Do not embed the whole database row-by-row.

Embed database meaning:

```text
1. Table documents
2. Column documents
3. Metric/business rule documents
4. Join path documents
5. Important value/entity documents
```

Do not embed:

```text
- every transaction row
- every order row
- every order detail row
- every numeric value
- every date value
```

Only embed important values that users may mention by name, such as company names, customer names, product names, city names, category names, and status values.

---

## 8. Table Embedding Document

Example:

```text
TYPE: table
TABLE: cong_ty
MEANING: FMCG company, supplier, brand owner.
VIETNAMESE_ALIASES: công ty, doanh nghiệp, chủ thương hiệu, nhà cung cấp.
USE_WHEN: user asks about company, brand, supplier, FMCG company, product owner, doanh thu theo công ty.
COLUMNS: cong_ty_id, ten_cong_ty, nganh_hang.
JOINS: cong_ty to don_hang_ban, cong_ty to san_pham, cong_ty to nha_phan_phoi.
```

Metadata:

```json
{
  "type": "table",
  "table": "cong_ty"
}
```

---

## 9. Column Embedding Document

Example:

```text
TYPE: column
TABLE: don_hang_ban
COLUMN: ngay_dat_hang
MEANING: order date, sales order date, transaction date.
VIETNAMESE_ALIASES: ngày đặt hàng, ngày bán, ngày phát sinh đơn, thời gian bán.
USE_WHEN: user asks about today, this month, this year, date range, sales period.
```

Metadata:

```json
{
  "type": "column",
  "table": "don_hang_ban",
  "column": "ngay_dat_hang"
}
```

---

## 10. Metric Embedding Document

Example:

```text
TYPE: metric
METRIC: doanh_thu
ALIASES: doanh thu, doanh số, sales, revenue, tổng tiền bán hàng.
FORMULA: SUM(chi_tiet_don_hang_ban.so_luong * chi_tiet_don_hang_ban.don_gia)
REQUIRED_TABLES: don_hang_ban, chi_tiet_don_hang_ban.
REQUIRED_JOIN: don_hang_ban.don_hang_ban_id = chi_tiet_don_hang_ban.don_hang_ban_id.
USE_WHEN: user asks about revenue, sales amount, total sales, or money from orders.
```

Metadata:

```json
{
  "type": "metric",
  "metric": "doanh_thu",
  "required_tables": [
    "don_hang_ban",
    "chi_tiet_don_hang_ban"
  ]
}
```

---

## 11. Join Path Embedding Document

Example:

```text
TYPE: join_path
NAME: revenue_by_company
USE_WHEN: user asks doanh thu theo công ty, sales by company, revenue by brand.
REQUIRED_TABLES: cong_ty, don_hang_ban, chi_tiet_don_hang_ban.
JOINS:
cong_ty.cong_ty_id = don_hang_ban.cong_ty_id
don_hang_ban.don_hang_ban_id = chi_tiet_don_hang_ban.don_hang_ban_id
```

Metadata:

```json
{
  "type": "join_path",
  "name": "revenue_by_company",
  "tables": [
    "cong_ty",
    "don_hang_ban",
    "chi_tiet_don_hang_ban"
  ]
}
```

---

## 12. Value/Entity Embedding Document

Only embed important values.

Example:

```text
TYPE: value
VALUE: Cong ty FMCG An Phat
TABLE: cong_ty
COLUMN: ten_cong_ty
ID_COLUMN: cong_ty_id
ID_VALUE: CTY_001
ALIASES: An Phat, FMCG An Phat, Công ty An Phát.
USE_WHEN: user mentions An Phat or Công ty An Phát.
```

Metadata:

```json
{
  "type": "value",
  "table": "cong_ty",
  "column": "ten_cong_ty",
  "id_column": "cong_ty_id",
  "id_value": "CTY_001",
  "value": "Cong ty FMCG An Phat"
}
```

---

## 13. Embedding Index Design

Use one vector index with metadata filtering.

Each document should look like this:

```json
{
  "id": "metric:doanh_thu",
  "text": "TYPE: metric\nMETRIC: doanh_thu\nALIASES: doanh thu, doanh số...",
  "metadata": {
    "type": "metric",
    "metric": "doanh_thu",
    "required_tables": [
      "don_hang_ban",
      "chi_tiet_don_hang_ban"
    ]
  }
}
```

Recommended metadata `type` values:

```text
table
column
metric
join_path
value
```

---

# Part C — Conversation Memory

## 14. Why Conversation Memory Is Needed

For back-and-forth SQL chat, the app must know what happened before.

The user may ask:

```text
what did you query?
now only in HCM
what about last month?
which one is highest?
what products did they buy?
why is this customer top 1?
```

These cannot be handled by schema retrieval alone.

The system must store:

```text
- previous user question
- standalone interpreted question
- generated SQL
- selected tables
- selected columns
- selected metrics
- selected filters
- result columns
- result preview
- result summary
- important result entities
```

---

## 15. Conversation Memory Object

After each SQL turn, store something like:

```json
{
  "turn_id": "turn_001",
  "user_question": "Top 10 khách hàng có doanh thu cao nhất tháng này",
  "intent": "NEW_QUERY",
  "standalone_question": "Top 10 customers by revenue this month",
  "selected_tables": [
    "khach_hang",
    "don_hang_ban",
    "chi_tiet_don_hang_ban"
  ],
  "selected_metrics": [
    "doanh_thu"
  ],
  "selected_dimensions": [
    "khach_hang.khach_hang_id",
    "khach_hang.ten_khach_hang"
  ],
  "filters": [
    "month = current month"
  ],
  "generated_sql": "SELECT ...",
  "result_columns": [
    "khach_hang_id",
    "ten_khach_hang",
    "doanh_thu"
  ],
  "result_preview": [
    {
      "khach_hang_id": "KH_001",
      "ten_khach_hang": "Tap Hoa Minh Anh",
      "doanh_thu": 12000000
    }
  ],
  "result_entities": [
    {
      "type": "customer",
      "id_column": "khach_hang_id",
      "id_value": "KH_001",
      "name_column": "ten_khach_hang",
      "name_value": "Tap Hoa Minh Anh"
    }
  ],
  "result_summary": "The query returned the top 10 customers by revenue for the current month.",
  "created_at": "2026-07-01T10:00:00"
}
```

---

## 16. Memory Window Sent to the Model

Do not send the entire conversation history.

Send compact memory:

```text
PREVIOUS QUERY MEMORY:
Last user question: Top 10 khách hàng có doanh thu cao nhất tháng này
Standalone question: Top 10 customers by revenue this month
Previous SQL: SELECT ...
Previous tables: khach_hang, don_hang_ban, chi_tiet_don_hang_ban
Previous metric: doanh_thu
Previous filters: current month
Previous result columns: khach_hang_id, ten_khach_hang, doanh_thu
Previous result preview:
1. KH_001 | Tap Hoa Minh Anh | 12000000
2. KH_002 | Sieu Thi Hoa Binh | 10500000
Previous result summary: Top 10 customers by revenue this month.
```

---

# Part D — Conversational Intent Types

## 17. Intent Categories

Every user message should be classified into one of these:

```text
NEW_QUERY
REFINE_PREVIOUS_QUERY
ASK_ABOUT_PREVIOUS_SQL
ASK_ABOUT_PREVIOUS_RESULT
DRILL_DOWN_PREVIOUS_RESULT
EXPLAIN_PREVIOUS_RESULT
INSUFFICIENT_CONTEXT
```

---

## 18. Intent Meaning

### 18.1 NEW_QUERY

The user asks a completely new database question.

Example:

```text
Doanh thu theo công ty trong tháng này
```

Action:

```text
Retrieve schema normally from current message.
Generate new SQL.
```

---

### 18.2 REFINE_PREVIOUS_QUERY

The user modifies the previous query.

Examples:

```text
now only in HCM
what about last month?
sort by revenue descending
only active customers
```

Action:

```text
Keep previous tables and filters when relevant.
Retrieve only extra schema needed for new condition.
Generate updated SQL.
```

---

### 18.3 ASK_ABOUT_PREVIOUS_SQL

The user asks about the SQL or queried tables.

Examples:

```text
what did you query?
show me the SQL
which tables did you use?
```

Action:

```text
No new retrieval.
No new SQL generation needed unless the user asks to rewrite the SQL.
Answer from memory.
```

---

### 18.4 ASK_ABOUT_PREVIOUS_RESULT

The user asks about the previous result.

Examples:

```text
which one is highest?
which customer is top 1?
how many rows did it return?
```

Action:

```text
No new retrieval if the answer exists in result memory.
Answer from previous result preview/summary.
```

---

### 18.5 DRILL_DOWN_PREVIOUS_RESULT

The user asks for new relevant data about something from the previous result.

Examples:

```text
what products did they buy?
show orders of the top customer
which routes does that customer belong to?
```

Action:

```text
Keep previous entity and filters.
Retrieve extra tables needed for the new detail.
Generate new SQL.
```

---

### 18.6 EXPLAIN_PREVIOUS_RESULT

The user asks for interpretation.

Examples:

```text
why is this customer top 1?
what does this mean?
explain the result
```

Action:

```text
Answer from result memory if enough.
If more data is required, generate a drill-down SQL query.
```

---

### 18.7 INSUFFICIENT_CONTEXT

The user references something unclear.

Example:

```text
what about that one?
```

When there are multiple possible references, return a clarification request or safe insufficient-context response.

---

# Part E — Retrieval Strategy

## 19. Basic Retrieval Rule

Do not always retrieve more tables.

Use this rule:

```text
If user asks about previous SQL/result:
    retrieve 0 new tables

If user refines previous query:
    keep previous tables
    retrieve only extra tables needed for the new condition

If user asks a new question:
    retrieve normally

If user drills down into previous result:
    keep previous tables/entity/filter
    retrieve extra tables needed for the drill-down
```

---

## 20. Retrieval Query Construction

### NEW_QUERY

```text
retrieval_query = current_user_message
```

Example:

```text
Doanh thu theo công ty trong tháng này
```

---

### REFINE_PREVIOUS_QUERY

```text
retrieval_query = previous_standalone_question + current_user_message
```

Example:

```text
Previous: Top 10 khách hàng có doanh thu cao nhất tháng này
Current: now only in HCM
Retrieval query: Top 10 khách hàng có doanh thu cao nhất tháng này now only in HCM
```

---

### DRILL_DOWN_PREVIOUS_RESULT

```text
retrieval_query = previous_result_entities + previous_filters + current_user_message
```

Example:

```text
Previous top customer: KH_001, Tap Hoa Minh Anh
Previous filter: current month
Current: what products did they buy?
Retrieval query: customer KH_001 Tap Hoa Minh Anh current month products bought
```

---

### ASK_ABOUT_PREVIOUS_SQL

```text
retrieval_query = none
```

---

### ASK_ABOUT_PREVIOUS_RESULT

```text
retrieval_query = none, unless the result preview is insufficient
```

---

## 21. Query Normalization and Expansion

Original message:

```text
Top 10 khách hàng có doanh thu cao nhất tháng này
```

Normalized:

```text
top 10 khach hang co doanh thu cao nhat thang nay
```

Expanded:

```text
Top 10 khách hàng có doanh thu cao nhất tháng này
top 10 khach hang co doanh thu cao nhat thang nay
customer revenue sales doanh_thu this month
khach_hang doanh_thu ngay_dat_hang
```

Use the expanded query for embedding retrieval.

---

## 22. Retrieval by Document Type

Recommended `top_k`:

```text
table docs: top 5
column docs: top 10
metric docs: top 3
join_path docs: top 3
value docs: top 5
```

Pseudo-code:

```python
retrieved = {
    "tables": search(query_embedding, type="table", top_k=5),
    "columns": search(query_embedding, type="column", top_k=10),
    "metrics": search(query_embedding, type="metric", top_k=3),
    "join_paths": search(query_embedding, type="join_path", top_k=3),
    "values": search(query_embedding, type="value", top_k=5),
}
```

---

## 23. Table Selection Rules

Collect required tables from:

```text
1. Retrieved table documents
2. Retrieved column documents
3. Retrieved metric required_tables
4. Retrieved join_path tables
5. Retrieved value/entity documents
6. Previous query memory if follow-up
```

Then remove irrelevant tables.

Preferred final table count:

```text
Best: 3–6 compact tables
Okay: 7–10 compact tables
Risky: more than 10 tables
```

---

## 24. Pinned Tables for Follow-Up Questions

For follow-up turns, previous tables should be pinned.

Example:

Previous query used:

```text
khach_hang
don_hang_ban
chi_tiet_don_hang_ban
```

User says:

```text
only in HCM
```

If retrieval uses only `only in HCM`, it may retrieve only location tables and lose revenue tables.

So use:

```text
pinned_tables = previous selected tables
new_tables = retrieved tables from current concept
final_tables = pinned_tables + new_tables + required bridge tables
```

---

## 25. Join Expansion

After final tables are selected, use allowed joins from `skill.md` or FK graph.

Example final tables:

```text
khach_hang
don_hang_ban
chi_tiet_don_hang_ban
```

Required joins:

```text
khach_hang.khach_hang_id = don_hang_ban.khach_hang_id
don_hang_ban.don_hang_ban_id = chi_tiet_don_hang_ban.don_hang_ban_id
```

Only send joins needed to connect selected tables.

---

# Part F — Building the LLM Skill Context

## 26. What the Model Should Receive

The LLM should receive a compact skill context, not the full `skill.md`.

The context should include:

```text
1. SQL dialect
2. global rules
3. conversation memory
4. current user message
5. standalone question if available
6. retrieved metric rules
7. relevant tables
8. relevant columns
9. allowed joins
10. matched values/entities
11. output JSON format
```

---

## 27. LLM Skill Context Template

```text
DATABASE SKILL CONTEXT

SQL DIALECT:
MySQL

GLOBAL RULES:
- Use only the provided tables.
- Use only the provided columns.
- Use only the provided joins.
- Do not invent tables.
- Do not invent columns.
- Do not invent join conditions.
- Use exact table and column names.
- Database identifiers use Vietnamese không dấu with snake_case.
- User questions may be written in Vietnamese có dấu.
- If the user asks for revenue / doanh thu / doanh số / sales, use the provided revenue formula.
- If the user asks for a time period, use the provided date column.

CONVERSATION MEMORY:
{conversation_memory}

CURRENT USER MESSAGE:
{user_message}

STANDALONE QUESTION CANDIDATE:
{standalone_question_candidate}

RETRIEVED METRIC RULES:
{metric_context}

RELEVANT TABLES:
{table_context}

RELEVANT COLUMNS:
{column_context}

ALLOWED JOINS:
{join_context}

MATCHED VALUES:
{value_context}
```

---

## 28. Example LLM Skill Context

User message:

```text
Top 10 khách hàng có doanh thu cao nhất tháng này
```

Context:

```text
DATABASE SKILL CONTEXT

SQL DIALECT:
MySQL

GLOBAL RULES:
- Use only the provided tables.
- Use only the provided columns.
- Use only the provided joins.
- Do not invent tables.
- Do not invent columns.
- Do not invent join conditions.
- Use exact table and column names.
- For doanh thu, use the provided formula.
- For "tháng này", filter by don_hang_ban.ngay_dat_hang.

CONVERSATION MEMORY:
No previous SQL query.

CURRENT USER MESSAGE:
Top 10 khách hàng có doanh thu cao nhất tháng này

STANDALONE QUESTION CANDIDATE:
Top 10 khách hàng có doanh thu cao nhất tháng này

RETRIEVED METRIC RULES:
Metric: doanh_thu
Aliases: doanh thu, doanh số, sales, revenue, tổng tiền bán hàng.
Formula:
SUM(chi_tiet_don_hang_ban.so_luong * chi_tiet_don_hang_ban.don_gia)
Required tables:
- don_hang_ban
- chi_tiet_don_hang_ban

RELEVANT TABLES:
Table: khach_hang
Meaning: customer / retailer / shop.
Columns:
- khach_hang_id: primary key
- ten_khach_hang: customer name
- customer_status: customer status

Table: don_hang_ban
Meaning: sales order header.
Columns:
- don_hang_ban_id: primary key
- khach_hang_id: customer foreign key
- ngay_dat_hang: order date
- trang_thai_don_hang: order status

Table: chi_tiet_don_hang_ban
Meaning: sales order line detail.
Columns:
- chi_tiet_don_hang_ban_id: primary key
- don_hang_ban_id: sales order foreign key
- so_luong: quantity sold
- don_gia: unit price

RELEVANT COLUMNS:
- khach_hang.khach_hang_id: customer ID
- khach_hang.ten_khach_hang: customer name
- don_hang_ban.don_hang_ban_id: order ID
- don_hang_ban.khach_hang_id: customer foreign key
- don_hang_ban.ngay_dat_hang: order date
- chi_tiet_don_hang_ban.don_hang_ban_id: order foreign key
- chi_tiet_don_hang_ban.so_luong: quantity sold
- chi_tiet_don_hang_ban.don_gia: unit price

ALLOWED JOINS:
- khach_hang.khach_hang_id = don_hang_ban.khach_hang_id
- don_hang_ban.don_hang_ban_id = chi_tiet_don_hang_ban.don_hang_ban_id

MATCHED VALUES:
None
```

---

# Part G — One LLM Prompt

## 29. Why JSON Output Is Better

Because the pipeline is conversational, the LLM should not always return raw SQL only.

It must tell the backend:

```text
- what intent it detected
- whether SQL is needed
- what standalone question it resolved
- what answer can be given from memory
- what SQL should be executed if needed
- what memory should be updated
```

Therefore, use JSON.

---

## 30. Final Model Prompt

```text
You are a conversational SQL agent.

Your job:
1. Decide whether the current user message is a new database query, a refinement of the previous query, a question about previous SQL, a question about previous results, a drill-down into previous results, or an explanation request.
2. If SQL is needed, generate one executable MySQL query.
3. If SQL is not needed, answer from conversation memory.
4. Use only the provided schema, joins, metrics, and values.
5. Do not invent tables, columns, joins, result rows, or values.

Return valid JSON only.
Do not use markdown.
Do not include explanation outside JSON.

Allowed intents:
- NEW_QUERY
- REFINE_PREVIOUS_QUERY
- ASK_ABOUT_PREVIOUS_SQL
- ASK_ABOUT_PREVIOUS_RESULT
- DRILL_DOWN_PREVIOUS_RESULT
- EXPLAIN_PREVIOUS_RESULT
- INSUFFICIENT_CONTEXT

Rules:
- If needs_sql is true, sql must contain one executable MySQL SELECT query.
- If needs_sql is false, sql must be null.
- If answering from previous memory, use only provided conversation memory.
- If the question cannot be answered using memory or provided schema context, use INSUFFICIENT_CONTEXT.
- Never generate INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE, or CREATE.

DATABASE SKILL CONTEXT:
{llm_skill_context}

Return JSON with this exact shape:
{
  "intent": "NEW_QUERY | REFINE_PREVIOUS_QUERY | ASK_ABOUT_PREVIOUS_SQL | ASK_ABOUT_PREVIOUS_RESULT | DRILL_DOWN_PREVIOUS_RESULT | EXPLAIN_PREVIOUS_RESULT | INSUFFICIENT_CONTEXT",
  "needs_sql": true,
  "standalone_question": "resolved standalone database question or null",
  "answer_from_memory": "answer if no SQL is needed, otherwise null",
  "sql": "one executable MySQL SELECT query if needed, otherwise null",
  "used_previous_context": true,
  "memory_update": {
    "selected_tables": [],
    "selected_columns": [],
    "selected_metrics": [],
    "selected_filters": [],
    "referenced_previous_entities": []
  }
}
```

---

## 31. Example LLM Output for New Query

```json
{
  "intent": "NEW_QUERY",
  "needs_sql": true,
  "standalone_question": "Top 10 khách hàng có doanh thu cao nhất tháng này",
  "answer_from_memory": null,
  "sql": "SELECT khach_hang.khach_hang_id, khach_hang.ten_khach_hang, SUM(chi_tiet_don_hang_ban.so_luong * chi_tiet_don_hang_ban.don_gia) AS doanh_thu FROM khach_hang JOIN don_hang_ban ON khach_hang.khach_hang_id = don_hang_ban.khach_hang_id JOIN chi_tiet_don_hang_ban ON don_hang_ban.don_hang_ban_id = chi_tiet_don_hang_ban.don_hang_ban_id WHERE don_hang_ban.ngay_dat_hang >= DATE_FORMAT(CURDATE(), '%Y-%m-01') AND don_hang_ban.ngay_dat_hang < DATE_ADD(DATE_FORMAT(CURDATE(), '%Y-%m-01'), INTERVAL 1 MONTH) GROUP BY khach_hang.khach_hang_id, khach_hang.ten_khach_hang ORDER BY doanh_thu DESC LIMIT 10",
  "used_previous_context": false,
  "memory_update": {
    "selected_tables": [
      "khach_hang",
      "don_hang_ban",
      "chi_tiet_don_hang_ban"
    ],
    "selected_columns": [
      "khach_hang.khach_hang_id",
      "khach_hang.ten_khach_hang",
      "don_hang_ban.ngay_dat_hang",
      "chi_tiet_don_hang_ban.so_luong",
      "chi_tiet_don_hang_ban.don_gia"
    ],
    "selected_metrics": [
      "doanh_thu"
    ],
    "selected_filters": [
      "current month"
    ],
    "referenced_previous_entities": []
  }
}
```

---

## 32. Example LLM Output for Asking About Previous SQL

User:

```text
what did you query?
```

Output:

```json
{
  "intent": "ASK_ABOUT_PREVIOUS_SQL",
  "needs_sql": false,
  "standalone_question": null,
  "answer_from_memory": "I queried khach_hang, don_hang_ban, and chi_tiet_don_hang_ban. The SQL calculated doanh_thu using SUM(chi_tiet_don_hang_ban.so_luong * chi_tiet_don_hang_ban.don_gia), grouped by customer, filtered to the current month, ordered by revenue descending, and limited to 10 rows.",
  "sql": null,
  "used_previous_context": true,
  "memory_update": {
    "selected_tables": [],
    "selected_columns": [],
    "selected_metrics": [],
    "selected_filters": [],
    "referenced_previous_entities": []
  }
}
```

---

## 33. Example LLM Output for Refinement

Previous query:

```text
Top 10 khách hàng có doanh thu cao nhất tháng này
```

User:

```text
now only in HCM
```

Output:

```json
{
  "intent": "REFINE_PREVIOUS_QUERY",
  "needs_sql": true,
  "standalone_question": "Top 10 khách hàng có doanh thu cao nhất tháng này tại HCM",
  "answer_from_memory": null,
  "sql": "SELECT ... WHERE ... AND khach_hang.thanh_pho = 'HCM' ...",
  "used_previous_context": true,
  "memory_update": {
    "selected_tables": [
      "khach_hang",
      "don_hang_ban",
      "chi_tiet_don_hang_ban"
    ],
    "selected_columns": [
      "khach_hang.thanh_pho"
    ],
    "selected_metrics": [
      "doanh_thu"
    ],
    "selected_filters": [
      "current month",
      "HCM"
    ],
    "referenced_previous_entities": []
  }
}
```

---

## 34. Example LLM Output for Drill-Down

Previous result:

```text
Top customer: KH_001, Tap Hoa Minh Anh
```

User:

```text
what products did they buy?
```

Output:

```json
{
  "intent": "DRILL_DOWN_PREVIOUS_RESULT",
  "needs_sql": true,
  "standalone_question": "List products bought by customer KH_001 in the same time period as the previous query",
  "answer_from_memory": null,
  "sql": "SELECT san_pham.san_pham_id, san_pham.ten_san_pham, SUM(chi_tiet_don_hang_ban.so_luong) AS so_luong_ban, SUM(chi_tiet_don_hang_ban.so_luong * chi_tiet_don_hang_ban.don_gia) AS doanh_thu FROM don_hang_ban JOIN chi_tiet_don_hang_ban ON don_hang_ban.don_hang_ban_id = chi_tiet_don_hang_ban.don_hang_ban_id JOIN san_pham ON chi_tiet_don_hang_ban.san_pham_id = san_pham.san_pham_id WHERE don_hang_ban.khach_hang_id = 'KH_001' AND don_hang_ban.ngay_dat_hang >= DATE_FORMAT(CURDATE(), '%Y-%m-01') AND don_hang_ban.ngay_dat_hang < DATE_ADD(DATE_FORMAT(CURDATE(), '%Y-%m-01'), INTERVAL 1 MONTH) GROUP BY san_pham.san_pham_id, san_pham.ten_san_pham ORDER BY doanh_thu DESC",
  "used_previous_context": true,
  "memory_update": {
    "selected_tables": [
      "don_hang_ban",
      "chi_tiet_don_hang_ban",
      "san_pham"
    ],
    "selected_columns": [
      "san_pham.san_pham_id",
      "san_pham.ten_san_pham",
      "chi_tiet_don_hang_ban.so_luong",
      "chi_tiet_don_hang_ban.don_gia"
    ],
    "selected_metrics": [
      "doanh_thu",
      "so_luong_ban"
    ],
    "selected_filters": [
      "khach_hang_id = KH_001",
      "current month"
    ],
    "referenced_previous_entities": [
      "KH_001"
    ]
  }
}
```

---

# Part H — SQL Validation and Execution

## 35. Validate Before Execution

Before running SQL:

```text
1. Parse SQL using SQLGlot or another SQL parser.
2. Confirm it is SELECT only.
3. Reject dangerous statements:
   - INSERT
   - UPDATE
   - DELETE
   - DROP
   - ALTER
   - TRUNCATE
   - CREATE
4. Confirm all tables are in allowed context.
5. Confirm all columns are in allowed context.
6. Confirm joins are allowed.
7. Add LIMIT if missing for exploratory queries.
```

---

## 36. Execute SQL

After validation:

```text
Run SQL against database.
Fetch result rows.
Limit preview rows for memory.
Return result to user.
```

Recommended:

```text
Result preview stored in memory: first 20–50 rows
Full result: optionally stream/export/paginate
```

---

## 37. Store Memory After Execution

After executing SQL, store:

```text
- current user question
- resolved standalone question
- intent
- generated SQL
- selected tables
- selected columns
- selected metrics
- filters
- result columns
- result preview
- result entities
- result summary
```

---

# Part I — Detailed Runtime Logic

## 38. Main Runtime Pseudo-Code

```python
def handle_user_message(conversation_id: str, user_message: str):
    # 1. Load previous memory
    memory = conversation_store.load_recent(conversation_id)

    # 2. Build current memory context
    memory_context = memory_builder.build_compact_memory(memory)

    # 3. Normalize and expand current user message
    normalized_message = normalize_vietnamese(user_message)
    expanded_message = expand_query(user_message, normalized_message)

    # 4. Create retrieval query based on previous memory
    retrieval_plan = build_retrieval_plan(
        user_message=user_message,
        expanded_message=expanded_message,
        memory=memory
    )

    # 5. Retrieve database skill context if needed
    if retrieval_plan.needs_retrieval:
        retrieved_items = retrieve_skill_items(
            query=retrieval_plan.retrieval_query,
            pinned_tables=retrieval_plan.pinned_tables
        )
    else:
        retrieved_items = []

    # 6. Resolve final tables, columns, metrics, joins, values
    resolved_context = context_builder.resolve(
        retrieved_items=retrieved_items,
        memory=memory,
        retrieval_plan=retrieval_plan
    )

    # 7. Build compact LLM skill context
    llm_skill_context = context_builder.build_llm_skill_context(
        user_message=user_message,
        memory_context=memory_context,
        resolved_context=resolved_context
    )

    # 8. Build one model prompt
    prompt = prompt_builder.build_conversational_sql_prompt(llm_skill_context)

    # 9. Call LLM once
    raw_response = llm_client.call(prompt)

    # 10. Parse JSON response
    model_response = response_parser.parse_json(raw_response)

    # 11. If no SQL needed, answer from memory
    if not model_response["needs_sql"]:
        conversation_store.save_non_sql_turn(
            conversation_id=conversation_id,
            user_message=user_message,
            model_response=model_response
        )
        return model_response["answer_from_memory"]

    # 12. Validate SQL
    sql = model_response["sql"]
    validation_result = sql_validator.validate(
        sql=sql,
        allowed_tables=resolved_context.tables,
        allowed_columns=resolved_context.columns,
        allowed_joins=resolved_context.joins
    )

    if not validation_result.ok:
        return {
            "error": "SQL_VALIDATION_FAILED",
            "reason": validation_result.reason
        }

    # 13. Execute SQL
    result = query_runner.execute(sql)

    # 14. Summarize result and extract result entities
    result_summary = result_summarizer.summarize(result)
    result_entities = result_summarizer.extract_entities(result)

    # 15. Store updated memory
    conversation_store.save_sql_turn(
        conversation_id=conversation_id,
        user_message=user_message,
        model_response=model_response,
        sql=sql,
        result=result,
        result_summary=result_summary,
        result_entities=result_entities
    )

    # 16. Return result to user
    return {
        "sql": sql,
        "rows": result.preview_rows,
        "summary": result_summary
    }
```

---

## 39. Retrieval Planning Pseudo-Code

```python
def build_retrieval_plan(user_message, expanded_message, memory):
    last_turn = memory.last_sql_turn()

    # Heuristic examples before LLM:
    asks_about_sql = contains_any(user_message, [
        "what did you query",
        "show me the sql",
        "which table",
        "câu sql",
        "truy vấn gì"
    ])

    asks_about_result = contains_any(user_message, [
        "which one",
        "top one",
        "highest",
        "cao nhất",
        "kết quả trên",
        "result"
    ])

    if asks_about_sql:
        return RetrievalPlan(
            needs_retrieval=False,
            retrieval_query=None,
            pinned_tables=last_turn.selected_tables if last_turn else []
        )

    if asks_about_result:
        return RetrievalPlan(
            needs_retrieval=False,
            retrieval_query=None,
            pinned_tables=last_turn.selected_tables if last_turn else []
        )

    if last_turn and looks_like_follow_up(user_message):
        retrieval_query = (
            last_turn.standalone_question + " " +
            " ".join(last_turn.selected_tables) + " " +
            expanded_message
        )
        return RetrievalPlan(
            needs_retrieval=True,
            retrieval_query=retrieval_query,
            pinned_tables=last_turn.selected_tables
        )

    return RetrievalPlan(
        needs_retrieval=True,
        retrieval_query=expanded_message,
        pinned_tables=[]
    )
```

---

# Part J — Example Full Conversations

## 40. Conversation Example 1: New Query → Ask SQL → Refine

### Turn 1

User:

```text
Top 10 khách hàng có doanh thu cao nhất tháng này
```

System:

```text
- intent: NEW_QUERY
- retrieve tables: khach_hang, don_hang_ban, chi_tiet_don_hang_ban
- generate SQL
- execute SQL
- store result memory
```

---

### Turn 2

User:

```text
what did you query?
```

System:

```text
- intent: ASK_ABOUT_PREVIOUS_SQL
- no retrieval
- no SQL execution
- answer from memory
```

---

### Turn 3

User:

```text
now only in HCM
```

System:

```text
- intent: REFINE_PREVIOUS_QUERY
- pin previous tables
- retrieve extra location/customer city context
- generate updated SQL
- execute SQL
- update memory
```

---

## 41. Conversation Example 2: New Query → Ask Result → Drill Down

### Turn 1

User:

```text
Top 5 công ty có doanh thu cao nhất tháng này
```

System:

```text
- retrieve cong_ty, don_hang_ban, chi_tiet_don_hang_ban
- generate SQL
- execute SQL
- store top company entities
```

---

### Turn 2

User:

```text
which one is highest?
```

System:

```text
- no retrieval
- answer from previous result preview
```

---

### Turn 3

User:

```text
what products made that revenue?
```

System:

```text
- intent: DRILL_DOWN_PREVIOUS_RESULT
- use previous top company entity
- keep previous time filter
- retrieve san_pham and chi_tiet_don_hang_ban if needed
- generate SQL
- execute SQL
- update memory
```

---

# Part K — Limits and Best Practices

## 42. Context Size Limits

Recommended final LLM context size:

```text
Small local model, 7B–12B:
- 3–6 tables preferred
- 8 tables maximum if compact

Bigger model, 30B+:
- 5–10 tables okay
- 10–15 compact tables possible
```

Avoid sending:

```text
- full skill.md
- full schema
- all table sample data
- all previous conversation messages
- all result rows
```

---

## 43. What to Send Per Table

Good compact table context:

```text
Table: khach_hang
Meaning: customer / retailer / shop
Columns:
- khach_hang_id: primary key
- ten_khach_hang: customer name
- customer_status: customer status
- thanh_pho: customer city
```

Avoid sending:

```text
- long business notes
- repeated aliases
- full sample data JSON
- every common value
```

---

## 44. Important Safety Rules

The SQL validator must enforce:

```text
- SELECT only
- no data modification
- no schema modification
- no unknown tables
- no unknown columns
- no invented joins
- limit large result sets
```

---

# Part L — Final Summary

## 45. Final Pipeline Idea

```text
skill.md is written as full database knowledge.
↓
skill.md is parsed into table, column, metric, join, and value embedding documents.
↓
User message comes in.
↓
System loads previous conversation memory.
↓
System decides retrieval mode.
↓
For new queries, retrieve normally.
↓
For follow-ups, pin previous tables and retrieve only new concepts.
↓
For questions about previous SQL/result, retrieve nothing and answer from memory.
↓
Build compact LLM skill context.
↓
Send one prompt to the LLM.
↓
LLM returns JSON with intent, needs_sql, answer_from_memory, SQL, and memory_update.
↓
If SQL is needed, validate and execute it.
↓
Store SQL, result preview, result summary, and entities in memory.
↓
Use that memory for the next turn.
```

The key principle:

```text
Conversation memory decides what to keep.
Embedding retrieval decides what to add.
Context builder decides what to send.
The LLM decides SQL vs memory answer in one call.
The backend validates, executes, and stores memory.
```

Phase 1: Database documentation
Write skill.md for tables, columns, metrics, joins, values, and aliases.

Phase 2: Embedding document builder
Convert skill.md + schema metadata into searchable embedding documents.

Phase 3: Vector index + retrieval
Embed table docs, column docs, metric docs, join docs, and value docs.

Phase 4: Conversation memory
Store previous user question, SQL, selected tables, filters, result columns, result preview, and result summary.

Phase 5: Intent + retrieval decision
Detect whether the user wants:
- a new query
- a refinement
- previous SQL
- previous result
- drill-down into previous result

Phase 6: Context builder
Build the compact skill context sent to the LLM:
- user question
- previous memory
- relevant tables
- relevant columns
- metric rules
- allowed joins
- matched values

Phase 7: One LLM call
Prompt the model to return structured JSON:
- intent
- needs_sql
- standalone_question
- SQL
- answer_from_memory

Phase 8: SQL validation, execution, and memory update
Validate SQL, execute it, return answer, then save the new query/result into memory.

