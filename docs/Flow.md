# System Architecture Overview

## 1) Client & Edge Layer

**(User → Internet → AWS Edge)**

**Client (Web / CLI / Backend)**

* Sends authenticated requests
* Uploads documents

**CloudFront + AWS WAF**

* Global edge endpoint
* Rate limiting & basic protection
* Forwards requests to API Gateway

**Purpose:**
Secure, low-latency entry point

---

## 2) API & Auth Layer

**(Request routing + identity)**

**API Gateway (HTTP API)**
**Routes:**

* `POST /upload-url`
* `POST /invoke`
* `POST /status`

**Cognito JWT Authorizer**

* Validates ID token
* Injects `tenant_id` + `user_id`

**API Key (`x-api-key`)**

* Identifies usage tier (free / pro)

**Purpose:**
Authenticate users, authorize tenants, apply quotas

---

## 3) Compute Layer

**(All business logic in one Lambda)**

**AWS Lambda (Private VPC)**
Handles:

* Presigned upload URLs
* Textract orchestration
* S3 persistence
* Bedrock calls
* Chunking + caching
* Usage metering

**Purpose:**
Single control plane for document intelligence

---

## 4) Storage Layer

**(Durable, deterministic processing)**

**S3 – `uploads/`**

* Raw PDFs / images
* Short TTL (7 days)

**S3 – `textract-results/`**

* `partial.txt` → incremental extraction
* `state.json` → pagination state
* `extracted.txt` → final OCR text
* `chunks/vX/*.txt` → stable chunk cache
* `answers/<hash>.json` → cached AI responses
* Longer TTL (30 days)

**Purpose:**
Make processing resumable, repeatable, and cheap

---

## 5) OCR Layer

**(Document text extraction)**

**Textract (Async for PDFs)**

* Triggered via `/invoke`
* SNS used only to signal completion
* `/status` paginates results incrementally

**Textract (Sync for Images)**

* Immediate OCR in Lambda

**Purpose:**
Scalable OCR without Lambda timeouts

---

## 6) AI Layer

**(Deterministic LLM processing)**

**Amazon Bedrock – Claude Sonnet**

* Invoked only after OCR is finalized
* Map → Reduce summarisation
* Safe chunk sizes
* Prompt + model + version hashing

**Purpose:**
Reliable, auditable AI output

---

## 7) Data & Governance Layer

**(Multi-tenant safety)**

**DynamoDB – `api-keys` table**

* Maps API keys → tier

**DynamoDB – `usage` table**

* Tracks tokens per tenant / user / month
* Enforces limits before Bedrock calls

**Purpose:**
Cost control + tenant isolation

---

## 🔁 End-to-End Flow (Ultra-compact)

```text
Client → /upload-url
→ presigned S3 PUT

Client uploads PDF/image to S3

Client → /invoke

Image → sync Textract → Bedrock
PDF → async Textract → return job_id

Client polls /status
→ paginate Textract
→ persist text to S3
→ finalize extracted.txt

Lambda checks cache
→ if answer exists: return immediately
→ else: chunk → Bedrock → cache answer

Response returned
```

**Properties:**

* Deterministic
* Re-runnable
* Cheap on repeats

---

## ⭐ Why This Architecture Works Well

* No NAT required (VPC endpoints only)
* Resumable processing for huge PDFs
* Deterministic AI (same input → same output)
* Cheap re-queries via S3 answer cache
* Clear platform vs app separation
* Production-grade without being overbuilt

---

If you want, I can also:

* Turn this into a **diagram (Mermaid / draw.io)**
* Compress it into an **architecture README**
* Rewrite it as a **design doc or investor-friendly explainer**