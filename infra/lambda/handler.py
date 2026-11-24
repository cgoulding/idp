import base64
import boto3
import datetime as dt
import hashlib
import json
import os
import re
import time
import uuid
from botocore.exceptions import ClientError
from typing import Any, Dict, Optional, Tuple, List
from enum import Enum
from enum import StrEnum

# ---------- AWS clients ----------
# Region is automatically picked up by boto3 in Lambda via AWS_REGION/AWS_DEFAULT_REGION
s3 = boto3.client("s3")
ddb = boto3.client("dynamodb")
textract = boto3.client("textract")
bedrock = boto3.client("bedrock-runtime")

# ---------- Environment ----------
AWS_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "eu-west-1"

UPLOAD_BUCKET = os.environ["UPLOAD_BUCKET"]
BEDROCK_MODEL_ID = os.environ["BEDROCK_MODEL_ID"]
USAGE_TABLE = os.environ["USAGE_TABLE"]
API_KEYS_TABLE = os.environ["API_KEYS_TABLE"]

MONTHLY_TOKEN_FREE = int(os.environ["MONTHLY_TOKEN_FREE"])
MONTHLY_TOKEN_PRO = int(os.environ["MONTHLY_TOKEN_PRO"])

TEXTRACT_SNS_TOPIC_ARN = os.environ["TEXTRACT_SNS_TOPIC_ARN"]
TEXTRACT_ROLE_ARN = os.environ["TEXTRACT_ROLE_ARN"]
TEXTRACT_RESULTS_PREFIX = os.environ.get("TEXTRACT_RESULTS_PREFIX", "textract-results/")

CHUNKING_VERSION = "v1"
ANSWER_CACHE_SCHEMA = "v1"

class DocumentType(StrEnum):
    PAYSLIP = "PAYSLIP"
    UTILITY_BILL = "UTILITY_BILL"
    BANK_STATEMENT = "BANK_STATEMENT"

class SchemaType(StrEnum):
    NAME = "NAME"
    ADDRESS = "ADDRESS"
    PPS = "PPS"
    TRANSACTIONS = "TRANSACTIONS"

NAME_PROMPT = '''
You are extracting structured data from a document.

Task:
Identify the customer's first name and last name exactly as they appear in the document.

Mapping rules:
- Map the customer's first name to the field "firstName".
- Map the customer's last name to the field "lastName".

Rules:
- Use ONLY information explicitly present in the document.
- Do NOT infer or guess missing values.
- If a value cannot be determined, return an empty string.
- Do NOT include any additional fields.
- Return the result strictly as JSON matching the provided schema.

Document content:
{{DOCUMENT_TEXT}}
'''

PPS_PROMPT = '''
You are extracting structured data from a document.

Task:
Identify the person's PPS number as it appears in the document.

Mapping rules:
- Map the person's PPS number to the field "ppsNumber".

Rules:
- Use ONLY information explicitly present in the document.
- Do NOT infer or guess missing values.
- If a value cannot be determined, return an empty string.
- Do NOT include any additional fields.
- Return the result strictly as JSON matching the provided schema.

Document content:
{{DOCUMENT_TEXT}}
'''

ADDRESS_PROMPT = '''
You are extracting structured data from a document.

Task:
Identify the person's address as it appears in the document.

Mapping rules:
- Map the person's Address Line 1 to the field "addressLine1".
- Map the person's Address Line 2 to the field "addressLine2".
- Map the person's Address Line 3 to the field "addressLine3".
- Map the person's town or city to the field "townCity".
- Map the person's county or provence to the field "county".
- Map the person's eircode or zipcode to the field "eircode".
- Map the person's country to the field "country".

Rules:
- Use ONLY information explicitly present in the document.
- Do NOT infer or guess missing values.
- If a value cannot be determined, return an empty string.
- Do NOT include any additional fields.
- Return the result strictly as JSON matching the provided schema.

Document content:
{{DOCUMENT_TEXT}}
'''

BANK_STATEMENT_PROMPT = '''
You are a careful data extraction engine. Your task is to parse the following bank statement text and return ONLY valid JSON that conforms exactly to the provided schema.

IMPORTANT:
- Output MUST be valid JSON (no markdown, no commentary, no extra keys).
- Use the statement’s transaction table columns: Date, Details, Withdrawn, Paid In, Balance.
- Each transaction row MUST map into:
  - date (YYYY-MM-DD)
  - details (string)
  - withdrawn (money object) OPTIONAL
  - paidIn (money object) OPTIONAL
  - balance (money object) REQUIRED
- Only one of withdrawn or paidIn should be present per transaction (if both appear, choose the one that matches debit/credit meaning; if unclear, leave both out and set details to include "[AMBIGUOUS AMOUNT]" and continue).
- If an amount is missing/unreadable, omit withdrawn/paidIn and include a best-effort details string.
- Currency: default to EUR unless the statement explicitly indicates a different currency.
- Keep the original ordering of transactions as they appear on the statement.
- Do not invent transactions. Do not hallucinate missing rows.

DATA NORMALIZATION RULES:
1) Dates:
   - Convert statement dates into ISO format "YYYY-MM-DD".
   - If the statement date is missing a year, infer year from statement period (if present). If still unknown, set year to 1900 and append "[MISSING YEAR]" in details.
2) Money:
   - Parse amounts into numbers using "." as decimal separator.
   - Remove thousands separators ("," or spaces).
   - Example: "1,234.50" => 1234.50
   - Example: "1 234,50" => 1234.50
3) Details:
   - Preserve the transaction narrative as-is, but trim extra whitespace.
   - If OCR is messy, preserve the best readable text (do not try to “correct” names).
4) Withdrawn vs Paid In:
   - Withdrawn is debit (money out).
   - Paid In is credit (money in).
   - If there is a negative sign, treat it as withdrawn unless the statement explicitly uses signed credits/debits.

ACCOUNT / STATEMENT HEADER EXTRACTION:
- Extract account fields if present: accountHolder.name, iban, bic, accountNumber, accountName.
- Extract statement period if present: from, to.
- Extract closing balance date and amount if present. If not clearly stated, use the balance value from the final transaction as the closing balance and set closingBalance.date to statementPeriod.to (if available) otherwise to the date of final transaction.

VALIDATION / CONSISTENCY CHECKS (DO THESE BEFORE OUTPUT):
- Ensure transactions[].balance is present for every transaction.
- Ensure withdrawn and paidIn are not both present in the same transaction unless the statement clearly shows both (rare).
- If statementPeriod is present, ensure transaction dates fall within it; if not, still keep them but append "[OUTSIDE PERIOD]" to details.
- If you can compute summary totals, do so by summing paidIn and withdrawn across transactions. If not possible, omit summary entirely.
- If openingBalance is not clearly stated, omit openingBalance.
- closingBalance should match the final transaction’s balance when possible.

MAPPING RULES:
- Map the source input statement text as sourceStatementText

NOW PARSE THIS STATEMENT TEXT:
{{STATEMENT_TEXT}}
'''

PROMPT_REGISTRY: dict[tuple[DocumentType, SchemaType], str] = {
    (DocumentType.UTILITY_BILL, SchemaType.ADDRESS): ADDRESS_PROMPT,
    (DocumentType.UTILITY_BILL, SchemaType.NAME): NAME_PROMPT,
    (DocumentType.PAYSLIP, SchemaType.PPS): PPS_PROMPT,
    (DocumentType.PAYSLIP, SchemaType.NAME): NAME_PROMPT,
    (DocumentType.PAYSLIP, SchemaType.ADDRESS): ADDRESS_PROMPT,
    (DocumentType.BANK_STATEMENT, SchemaType.NAME): NAME_PROMPT,
    (DocumentType.BANK_STATEMENT, SchemaType.ADDRESS): ADDRESS_PROMPT,
    (DocumentType.BANK_STATEMENT, SchemaType.TRANSACTIONS): BANK_STATEMENT_PROMPT,
}

SCHEMA_REGISTRY: dict[SchemaType, dict[str, Any]] = {
    SchemaType.NAME: {
             "type": "object",
             "required": ["firstName", "lastName"],
             "additionalProperties": False,
             "properties": {
                 "firstName": {"type": "string"},
                 "lastName": {"type": "string"}
             }
           },
    SchemaType.ADDRESS: {
                "type": "object",
                "additionalProperties": False,
                "required": [
                  "addressLine1",
                  "townCity",
                  "country"
                ],
                "properties": {
                  "addressLine1": {
                    "type": "string",
                    "description": "Primary address line, e.g. house number and street or building name.",
                    "examples": ["12 Main Street"]
                  },
                  "addressLine2": {
                    "type": "string",
                    "description": "Secondary address line, e.g. apartment, unit, or locality.",
                    "examples": ["Apartment 4B"]
                  },
                  "addressLine3": {
                    "type": "string",
                    "description": "Additional address line if present.",
                    "examples": ["Smithfield"]
                  },
                  "townCity": {
                    "type": "string",
                    "description": "Town or city as stated on the bank statement.",
                    "examples": ["Dublin"]
                  },
                  "county": {
                    "type": "string",
                    "description": "Irish county, if present.",
                    "examples": ["Dublin"]
                  },
                  "eircode": {
                    "type": "string",
                    "description": "Irish Eircode if present on the statement.",
                    "examples": ["D02 X285"]
                  },
                  "country": {
                    "type": "string",
                    "description": "Country name as shown on the statement.",
                    "examples": ["Ireland"]
                  }
                }
              },
    SchemaType.PPS: {
            "type": "object",
            "required": ["ppsNumber"],
            "additionalProperties": False,
            "properties": {
                "ppsNumber": {"type": "string"}
            }
          },
    SchemaType.TRANSACTIONS: {
  "type": "object",
  "additionalProperties": False,
  "required": ["account", "statementPeriod", "transactions", "closingBalance"],
  "properties": {
    "sourceStatementText": {
                "type": "string",
                "description": "The source input statement text."
              },
    "statementMeta": {
      "type": "object",
      "additionalProperties": False,
      "description": "Metadata about the statement document itself.",
      "properties": {
        "issuer": {
          "type": "string",
          "description": "Bank name or issuer."
        },
        "statementType": {
          "type": "string",
          "description": "e.g., Current Account Statement."
        },
        "generatedAt": {
          "type": "string",
          "format": "date-time"
        },
        "pageCount": {
          "type": "integer",
          "minimum": 1
        },
        "source": {
          "type": "object",
          "additionalProperties": False,
          "properties": {
            "fileName": {
              "type": "string"
            },
            "fileHash": {
              "type": "string",
              "description": "Optional hash for integrity tracking."
            }
          }
        }
      }
    },

    "account": {
      "type": "object",
      "additionalProperties": False,
      "description": "Account identifying information shown on the statement.",
      "required": ["currency"],
      "properties": {
        "accountHolder": {
          "type": "object",
          "additionalProperties": False,
          "properties": {
            "name": {
              "type": "string"
            },
            "address": {
              "type": "object",
              "additionalProperties": False,
              "properties": {
                "line1": { "type": "string" },
                "line2": { "type": "string" },
                "line3": { "type": "string" },
                "town": { "type": "string" },
                "county": { "type": "string" },
                "eircode": { "type": "string" }
              }
            }
          }
        },
        "accountName": {
          "type": "string",
          "description": "If the statement shows a product/account name."
        },
        "accountNumber": {
          "type": "string",
          "description": "Domestic account number (format varies by bank)."
        },
        "sortCode": {
          "type": "string",
          "description": "Optional (often UK/NI); not always present in IE statements."
        },
        "iban": {
          "type": "string",
          "description": "International Bank Account Number.",
          "pattern": "^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$"
        },
        "bic": {
          "type": "string",
          "description": "Bank Identifier Code (SWIFT).",
          "pattern": "^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$"
        },
        "currency": {
          "type": "string",
          "description": "ISO 4217 currency code.",
          "pattern": "^[A-Z]{3}$",
          "examples": ["EUR"]
        }
      }
    },

    "transactions": {
      "type": "array",
      "description": "Line items from the statement. Matches statement columns: Date, Details, Withdrawn, Paid In, Balance.",
      "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["date", "details", "balance"],
        "properties": {
          "date": {
            "type": "string",
            "format": "date",
            "description": "Transaction date as shown on the statement."
          },
          "details": {
            "type": "string",
            "description": "Transaction details / narrative."
          },
          "withdrawn": {
            "type": "object",
            "additionalProperties": False,
            "required": ["amount", "currency"],
            "description": "Debit amount. Present when the transaction is money out.",
            "properties": {
              "amount": {
                "type": "number",
                "description": "Monetary amount as a number. Use positive values; debit/credit sign handled by context."
              },
              "currency": {
                "type": "string",
                "pattern": "^[A-Z]{3}$",
                "description": "ISO 4217 currency code."
              }
            }
          },
          "paidIn": {
            "type": "object",
            "additionalProperties": False,
            "required": ["amount", "currency"],
            "description": "Credit amount. Present when the transaction is money in.",
            "properties": {
              "amount": {
                "type": "number",
                "description": "Monetary amount as a number. Use positive values; debit/credit sign handled by context."
              },
              "currency": {
                "type": "string",
                "pattern": "^[A-Z]{3}$",
                "description": "ISO 4217 currency code."
              }
            }
          },
          "balance": {
            "type": "object",
            "additionalProperties": False,
            "required": ["amount", "currency"],
            "description": "Account balance after this transaction.",
            "properties": {
              "amount": {
                "type": "number"
              },
              "currency": {
                "type": "string",
                "pattern": "^[A-Z]{3}$"
              }
            }
          }
        }
      }
    },

    "statementPeriod": {
      "type": "object",
      "additionalProperties": False,
      "description": "The date range the statement covers.",
      "required": ["from", "to"],
      "properties": {
        "from": {
          "type": "string",
          "format": "date"
        },
        "to": {
          "type": "string",
          "format": "date"
        },
        "statementNumber": {
          "type": "string"
        }
      }
    },

    "openingBalance": {
      "type": "object",
      "additionalProperties": False,
      "required": ["date", "balance"],
      "properties": {
        "date": {
          "type": "string",
          "format": "date"
        },
        "balance": {
          "type": "object",
          "additionalProperties": False,
          "required": ["amount", "currency"],
          "properties": {
            "amount": {
              "type": "number"
            },
            "currency": {
              "type": "string",
              "pattern": "^[A-Z]{3}$"
            }
          }
        }
      }
    },

    "closingBalance": {
      "type": "object",
      "additionalProperties": False,
      "description": "Closing balance at end of statement period.",
      "required": ["date", "balance"],
      "properties": {
        "date": {
          "type": "string",
          "format": "date"
        },
        "balance": {
          "type": "object",
          "additionalProperties": False,
          "required": ["amount", "currency"],
          "properties": {
            "amount": {
              "type": "number"
            },
            "currency": {
              "type": "string",
              "pattern": "^[A-Z]{3}$"
            }
          }
        }
      }
    },

    "summary": {
      "type": "object",
      "additionalProperties": False,
      "description": "Optional totals across the statement period.",
      "properties": {
        "totalCredits": {
          "type": "object",
          "additionalProperties": False,
          "required": ["amount", "currency"],
          "properties": {
            "amount": { "type": "number" },
            "currency": {
              "type": "string",
              "pattern": "^[A-Z]{3}$"
            }
          }
        },
        "totalDebits": {
          "type": "object",
          "additionalProperties": False,
          "required": ["amount", "currency"],
          "properties": {
            "amount": { "type": "number" },
            "currency": {
              "type": "string",
              "pattern": "^[A-Z]{3}$"
            }
          }
        },
        "transactionCount": {
          "type": "integer",
          "minimum": 0
        }
      }
    }
  }
}
}

# ---------- Time helpers ----------
def _now_ms() -> int:
    return int(time.time() * 1000)


def _remaining_ms(context) -> int:
    if context is None:
        return 30_000
    try:
        return int(context.get_remaining_time_in_millis())
    except Exception:
        return 30_000


def _ms_since(start_ms: int) -> int:
    return max(0, _now_ms() - start_ms)


# ---------- HTTP helpers ----------
def _json(status: int, obj: Any, headers: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    return {"statusCode": status, "headers": h, "body": json.dumps(obj)}


def _parse_json_body(event):
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw).decode("utf-8", errors="replace")
        except Exception as e:
            print("Base64 decode failed:", str(e))
            return {}
    else:
        raw = event.get("body")

    if raw is None:
        print("No body in event")
        return {}

    if raw == "":
        print("Empty body string")
        return {}

    try:
        obj = json.loads(raw)
    except Exception as e:
        print("JSON parse failed:", str(e), "raw was:", repr(raw))
        return {}

    if not isinstance(obj, dict):
        print("Parsed JSON is not an object:", type(obj), "value:", repr(obj))
        return {}

    return obj


def _get_claims(event: Dict[str, Any]) -> Dict[str, Any]:
    # HTTP API JWT authorizer format
    try:
        return event["requestContext"]["authorizer"]["jwt"]["claims"]
    except Exception:
        return {}


def _get_tenant_user(claims: Dict[str, Any]) -> Tuple[str, str]:
    user_id = claims.get("sub", "unknown")
    tenant_id = claims.get("custom:tenant_id", "unknown")
    return tenant_id, user_id


def _month_key() -> str:
    return dt.datetime.utcnow().strftime("%Y-%m")


def _get_header(event: Dict[str, Any], name: str) -> Optional[str]:
    headers = event.get("headers") or {}
    if not headers:
        return None
    # Normalize casing
    for k, v in headers.items():
        if k.lower() == name.lower():
            return v
    return None


def _get_api_key(event: Dict[str, Any]) -> Optional[str]:
    return _get_header(event, "x-api-key")


# ---------- API key / tier ----------
def _get_tier_for_api_key(api_key: Optional[str]) -> Optional[str]:
    # hardcode for the moment
    return "pro"

def _get_tier_for_api_key_x(api_key: Optional[str]) -> Optional[str]:
    if not api_key:
        return None
    resp = ddb.get_item(TableName=API_KEYS_TABLE, Key={"api_key": {"S": api_key}})
    item = resp.get("Item")
    if not item:
        return None
    return item.get("tier", {}).get("S", "free")


def _tier_limit(tier: str) -> int:
    return MONTHLY_TOKEN_PRO if tier == "pro" else MONTHLY_TOKEN_FREE


# ---------- Token estimation ----------
def _estimate_tokens(text: str) -> int:
    # Very rough heuristic: ~4 chars per token
    return max(1, len(text or "") // 4)


def _normalize_prompt(prompt: str) -> str:
    p = (prompt or "").strip()
    p = p.replace("\r\n", "\n").replace("\r", "\n")
    p = re.sub(r"[ \t]+", " ", p)
    p = re.sub(r"\n{3,}", "\n\n", p).strip()
    return p


def _estimate_tokens_pair(prompt: str, doc_text: str) -> int:
    return _estimate_tokens(_normalize_prompt(prompt)) + _estimate_tokens(doc_text or "")


# ---------- Usage tracking (atomic) ----------
def _update_usage_and_check_atomic(
        tenant_id: str,
        user_id: str,
        month: str,
        tokens_add: int,
        limit: int,
) -> Tuple[bool, int]:
    """
    Atomically increments tokens_used and prevents exceeding limit.
    Returns (ok, used_after).
    """
    if tokens_add <= 0:
        tokens_add = 1

    # If a single request exceeds the whole limit, reject before touching DB.
    if tokens_add > limit:
        return False, limit + 1

    pk = f"TENANT#{tenant_id}"
    sk = f"USER#{user_id}#{month}"

    cap = limit - tokens_add  # tokens_used must be <= cap before adding tokens_add

    try:
        resp = ddb.update_item(
            TableName=USAGE_TABLE,
            Key={"pk": {"S": pk}, "sk": {"S": sk}},
            UpdateExpression="SET tokens_used = if_not_exists(tokens_used, :zero) + :inc, updated_at = :now",
            ConditionExpression="attribute_not_exists(tokens_used) OR tokens_used <= :cap",
            ExpressionAttributeValues={
                ":zero": {"N": "0"},
                ":inc": {"N": str(tokens_add)},
                ":cap": {"N": str(cap)},
                ":now": {"S": dt.datetime.utcnow().isoformat()},
            },
            ReturnValues="UPDATED_NEW",
        )
        used_after = int(resp["Attributes"]["tokens_used"]["N"])
        return True, used_after
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "ConditionalCheckFailedException":
            # Get current usage for better error responses (best-effort)
            cur = ddb.get_item(TableName=USAGE_TABLE, Key={"pk": {"S": pk}, "sk": {"S": sk}}).get("Item", {})
            used = int(cur.get("tokens_used", {"N": "0"})["N"])
            return False, used
        raise


# ---------- Text normalization / chunking ----------
def _normalize_whitespace(s: str) -> str:
    s = (s or "").replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s).strip()
    return s


def _chunk_text_by_chars(text: str, chunk_chars: int, overlap_chars: int) -> List[str]:
    if chunk_chars <= 0:
        return [text]
    chunks: List[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(n, start + chunk_chars)
        chunks.append(text[start:end])
        if end == n:
            break
        start = max(0, end - overlap_chars)
    return chunks

def _call_claude_text_only(
        prompt: str,
        output_schema: Dict[str, Any] = SCHEMA_REGISTRY[SchemaType.NAME],
        max_tokens: int = 600,
        temperature: float = 0.2
) -> Tuple[Dict[str, Any], str]:
    """
    Uses Bedrock Converse + Tool Use with a JSON schema tool to get reliable structured JSON output.
    Returns the tool 'input' (a dict) that should conform to the schema.
    """

    tool_name = "StructuredAnswer"

    # Tool config: define a tool whose "input" is the JSON you want, validated by schema
    tool_config = {
        "tools": [
            {
                "toolSpec": {
                    "name": tool_name,
                    "description": "Return the final answer as structured JSON matching the provided schema.",
                    "inputSchema": {"json": output_schema},
                }
            }
        ],
        # Force the model to call this tool (i.e., produce structured JSON)
        "toolChoice": {"tool": {"name": tool_name}},
    }

    resp = bedrock.converse(
        modelId=BEDROCK_MODEL_ID,
        messages=[
            {
                "role": "user",
                "content": [{"text": prompt}],
            }
        ],
        inferenceConfig={
            "maxTokens": int(max_tokens),
            "temperature": float(temperature),
        },
        toolConfig=tool_config,
    )

    # The model should respond with a toolUse content block that includes the JSON as `input`
    content = (resp.get("output") or {}).get("message", {}).get("content", []) or []

    for block in content:
        tool_use = block.get("toolUse")
        if tool_use and tool_use.get("name") == tool_name:
            structured = tool_use.get("input")
            if isinstance(structured, dict):
                return structured, json.dumps(content)
            raise ValueError(f"Tool input was not an object: {type(structured)}")

    # If we get here, the model didn't return a tool call (unexpected if toolChoice is enforced)
    raise ValueError(f"No toolUse block named {tool_name}. Full response: {json.dumps(resp)[:2000]}")

def _summarize_map_reduce(prompt: str, output_schema: dict[str, Any], document_text: str, context, max_tokens_final: int = 700) -> Tuple[str, Dict[str, Any]]:
    MAX_DOC_CHARS_FOR_AI = 250_000
    CHUNK_CHARS = 24_000
    OVERLAP_CHARS = 800
    MAP_MAX_TOKENS = 350
    REDUCE_MAX_TOKENS = max_tokens_final

    doc = (document_text or "")[:MAX_DOC_CHARS_FOR_AI]
    chunks = _chunk_text_by_chars(doc, CHUNK_CHARS, OVERLAP_CHARS)

    summaries: List[str] = []
    for idx, ch in enumerate(chunks, start=1):
        if _remaining_ms(context) < 7_000:
            break
        map_prompt = (
            "You are summarizing one chunk of a larger document.\n"
            f"User request:\n{prompt}\n\n"
            f"Chunk {idx}/{len(chunks)}:\n{ch}\n\n"
            "Return a concise structured summary with:\n"
            "- Key points\n- Entities\n- Numbers/dates\n- Risks/decisions\n"
            "Do not invent facts."
        )
        _, chunk_sum = _call_claude_text_only(map_prompt, output_schema, max_tokens=MAP_MAX_TOKENS)
        summaries.append(f"[Chunk {idx}]\n{chunk_sum.strip()}")

    reduce_prompt = (
            "You are combining summaries of document chunks into a final response.\n"
            f"User request:\n{prompt}\n\n"
            "Chunk summaries:\n"
            + "\n\n".join(summaries)
            + "\n\n"
              "Produce the final answer. Be faithful to the document. "
              "If information is missing, say so."
    )

    _, final = _call_claude_text_only(reduce_prompt, output_schema, max_tokens=REDUCE_MAX_TOKENS)
    stats = {"chunking_version": CHUNKING_VERSION, "chunks_total": len(chunks), "chunks_summarized": len(summaries)}
    return final, stats


# ---------- S3 helpers ----------
def _s3_put_text(key: str, text: str, content_type: str = "text/plain; charset=utf-8") -> None:
    s3.put_object(
        Bucket=UPLOAD_BUCKET,
        Key=key,
        Body=(text or "").encode("utf-8"),
        ContentType=content_type,
        ServerSideEncryption="AES256",
    )


def _s3_put_json(key: str, obj: Dict[str, Any]) -> None:
    _s3_put_text(key, json.dumps(obj), content_type="application/json; charset=utf-8")


def _s3_get_text_if_exists(key: str) -> Optional[str]:
    try:
        obj = s3.get_object(Bucket=UPLOAD_BUCKET, Key=key)
        return obj["Body"].read().decode("utf-8")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "NotFound", "404"):
            return None
        # AccessDenied etc. should not be swallowed
        raise
    except Exception:
        raise


def _s3_get_json_if_exists(key: str) -> Optional[Dict[str, Any]]:
    raw = _s3_get_text_if_exists(key)
    if raw is None:
        return None
    return json.loads(raw)


def _s3_exists(key: str) -> bool:
    try:
        s3.head_object(Bucket=UPLOAD_BUCKET, Key=key)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise
    except Exception:
        raise


def _s3_key_results(job_id: str) -> str:
    return f"{TEXTRACT_RESULTS_PREFIX}{job_id}/extracted.txt"


def _s3_key_partial(job_id: str) -> str:
    return f"{TEXTRACT_RESULTS_PREFIX}{job_id}/partial.txt"


def _s3_key_state(job_id: str) -> str:
    return f"{TEXTRACT_RESULTS_PREFIX}{job_id}/state.json"


def _s3_key_chunks_prefix(job_id: str) -> str:
    return f"{TEXTRACT_RESULTS_PREFIX}{job_id}/chunks/{CHUNKING_VERSION}/"


def _s3_key_chunk(job_id: str, idx: int) -> str:
    return f"{_s3_key_chunks_prefix(job_id)}{idx:04d}.txt"


def _prompt_hash(prompt: str, model_id: str) -> str:
    norm = _normalize_prompt(prompt)
    payload = f"{ANSWER_CACHE_SCHEMA}|{CHUNKING_VERSION}|{model_id}|{norm}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def _s3_key_answer(job_id: str, prompt_hash: str) -> str:
    return f"{TEXTRACT_RESULTS_PREFIX}{job_id}/answers/{prompt_hash}.json"


def _ensure_chunk_cache(job_id: str, stitched_text: str) -> Dict[str, Any]:
    MAX_DOC_CHARS_FOR_AI = 250_000
    CHUNK_CHARS = 24_000
    OVERLAP_CHARS = 800

    doc = (stitched_text or "")[:MAX_DOC_CHARS_FOR_AI]
    chunks = _chunk_text_by_chars(doc, CHUNK_CHARS, OVERLAP_CHARS)

    wrote = 0
    for i, ch in enumerate(chunks, start=1):
        k = _s3_key_chunk(job_id, i)
        if not _s3_exists(k):
            _s3_put_text(k, _normalize_whitespace(ch))
            wrote += 1

    return {
        "chunking_version": CHUNKING_VERSION,
        "chunks_total": len(chunks),
        "chunks_written_now": wrote,
        "chunk_keys_prefix": _s3_key_chunks_prefix(job_id),
        "chunk_chars": CHUNK_CHARS,
        "overlap_chars": OVERLAP_CHARS,
        "doc_chars_capped": len(doc),
    }


def _write_answer_cache(
        job_id: str,
        prompt_hash: str,
        prompt: str,
        answer: str,
        chunk_meta: Dict[str, Any],
        meta: Dict[str, Any],
) -> str:
    key = _s3_key_answer(job_id, prompt_hash)
    payload = {
        "schema": ANSWER_CACHE_SCHEMA,
        "chunking_version": CHUNKING_VERSION,
        "job_id": job_id,
        "prompt_hash": prompt_hash,
        "prompt_normalized": _normalize_prompt(prompt),
        "model": BEDROCK_MODEL_ID,
        "created_at": dt.datetime.utcnow().isoformat(),
        "chunking": chunk_meta,
        "meta": meta,
        "response": answer,
    }
    _s3_put_json(key, payload)
    return key


# ---------- Textract helpers ----------
def collect_text_from_get_document_text_detection(resp: Dict[str, Any]) -> str:
    lines = []
    for b in resp.get("Blocks", []):
        if b.get("BlockType") == "LINE" and "Text" in b:
            lines.append(b["Text"])
    return "\n".join(lines)


def _textract_sync_text_from_s3(key: str) -> str:
    resp = textract.detect_document_text(Document={"S3Object": {"Bucket": UPLOAD_BUCKET, "Name": key}})
    return collect_text_from_get_document_text_detection(resp)


def start_pdf_text_detection_async(s3_key: str) -> str:
    print(f"start_pdf_text_detection_async {s3_key}")
    resp = textract.start_document_text_detection(
        DocumentLocation={"S3Object": {"Bucket": UPLOAD_BUCKET, "Name": s3_key}},
        NotificationChannel={"SNSTopicArn": TEXTRACT_SNS_TOPIC_ARN, "RoleArn": TEXTRACT_ROLE_ARN},
    )
    print(f"started {resp['JobId']}")
    return resp["JobId"]


# ---------- API handlers ----------
def create_upload_url(event: Dict[str, Any]) -> Dict[str, Any]:
    body = _parse_json_body(event)
    content_type = body.get("content_type", "application/octet-stream")

    key = f"uploads/{uuid.uuid4()}"
    url = s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": UPLOAD_BUCKET, "Key": key, "ContentType": content_type},
        ExpiresIn=300,
    )
    return _json(200, {"upload_url": url, "s3_key": key, "bucket": UPLOAD_BUCKET})


def process_document(event: Dict[str, Any], context) -> Dict[str, Any]:
    body = _parse_json_body(event)
    s3_key = body.get("s3_key")
    document_type = DocumentType(body.get("document_type"))
    print(f'document_type {document_type}')
    schema_type = SchemaType(body.get("schema_type"))
    print(f'schema_type {schema_type}')
    schema = SCHEMA_REGISTRY[schema_type]
    prompt = PROMPT_REGISTRY[(document_type, schema_type)]
    print(f'prompt {prompt}')
    file_type = (body.get("file_type") or "pdf").lower()  # pdf|image
    print(f'file_type {file_type}')

    if not s3_key:
        return _json(400, {"error": "Missing s3_key"})

    if file_type not in ("pdf", "image"):
        return _json(400, {"error": "Invalid file_type. Must be 'pdf' or 'image'."})

    claims = _get_claims(event)
    print(f'claims {claims}')

    tenant_id, user_id = _get_tenant_user(claims)
    print(f'tenant_id {tenant_id} user_id {user_id}')

    api_key = _get_api_key(event)
    tier = _get_tier_for_api_key(api_key)
    if not tier:
        return _json(403, {"error": "Missing or invalid x-api-key"})

    prompt_norm = _normalize_prompt(prompt)
    print(f'prompt_norm {prompt_norm}')

    # Image => sync Textract => Bedrock now
    if file_type == "image":
        extracted_text = _textract_sync_text_from_s3(s3_key)

        tokens_in_est = _estimate_tokens_pair(prompt_norm, extracted_text)
        limit = _tier_limit(tier)

        ok, used = _update_usage_and_check_atomic(tenant_id, user_id, _month_key(), tokens_in_est, limit)
        if not ok:
            return _json(429, {"error": "Monthly token quota exceeded", "tier": tier, "used": used, "limit": limit})

        start_ms = _now_ms()
        final_answer, stats = _summarize_map_reduce(prompt_norm, schema, extracted_text, context=context, max_tokens_final=700)
        latency_ms = _ms_since(start_ms)

        return _json(
            200,
            {
                "mode": "sync",
                "file_type": "image",
                "tenant_id": tenant_id,
                "user_id": user_id,
                "tier": tier,
                "model": BEDROCK_MODEL_ID,
                "s3_key": s3_key,
                "meta": {"latency_ms": latency_ms, "tokens_in_est": tokens_in_est, "monthly_used_est": used},
                "response": final_answer,
                "chunking": stats,
            },
        )

    # PDF => async Textract start
    job_id = start_pdf_text_detection_async(s3_key)
    results_key = _s3_key_results(job_id)

    return _json(
        202,
        {
            "mode": "async",
            "file_type": "pdf",
            "job_id": job_id,
            "results_key": results_key,
            "message": "Textract job started. Call POST /status with job_id to paginate+persist extracted text and optionally run Bedrock.",
        }
    )


def status(event: Dict[str, Any], context) -> Dict[str, Any]:
    body = _parse_json_body(event)
    job_id = body.get("job_id")
    document_type = DocumentType(body.get("document_type"))
    schema_type = SchemaType(body.get("schema_type"))
    prompt = PROMPT_REGISTRY[(document_type, schema_type)]
    schema = SCHEMA_REGISTRY[schema_type]
    max_tokens = int(body.get("max_tokens", 700))
    run_bedrock = _as_bool(body.get("run_bedrock"), default=True)

    if not job_id:
        return _json(400, {"error": "Missing job_id"})

    claims = _get_claims(event)
    tenant_id, user_id = _get_tenant_user(claims)

    api_key = _get_api_key(event)
    tier = _get_tier_for_api_key(api_key)
    if not tier:
        return _json(403, {"error": "Missing or invalid x-api-key"})

    results_key = _s3_key_results(job_id)
    partial_key = _s3_key_partial(job_id)
    state_key = _s3_key_state(job_id)

    prompt_norm = _normalize_prompt(prompt)
    ph = _prompt_hash(prompt_norm, BEDROCK_MODEL_ID)
    answer_key = _s3_key_answer(job_id, ph)

    # FAST PATH: extracted exists => return cached answer or compute+cache
    extracted = _s3_get_text_if_exists(results_key)
    if extracted is not None:
        cached = _s3_get_json_if_exists(answer_key)
        if cached is not None:
            return _json(
                200,
                {
                    "job_id": job_id,
                    "status": "SUCCEEDED",
                    "results_key": results_key,
                    "answer_key": answer_key,
                    "cache": {"hit": True},
                    "meta": cached.get("meta", {}),
                    "response": cached.get("response", ""),
                },
            )

        stitched_text = extracted
        chunk_meta = _ensure_chunk_cache(job_id, stitched_text)

        if not run_bedrock:
            return _json(
                200,
                {
                    "job_id": job_id,
                    "status": "SUCCEEDED",
                    "results_key": results_key,
                    "cache": {"hit": False},
                    "chunking": chunk_meta,
                },
            )

        tokens_in_est = _estimate_tokens_pair(prompt_norm, stitched_text)
        limit = _tier_limit(tier)
        ok, used = _update_usage_and_check_atomic(tenant_id, user_id, _month_key(), tokens_in_est, limit)
        if not ok:
            return _json(429, {"error": "Monthly token quota exceeded", "tier": tier, "used": used, "limit": limit})

        start_ms = _now_ms()
        final_answer, mr_stats = _summarize_map_reduce(prompt_norm, schema, stitched_text, context, max_tokens_final=max_tokens)
        latency_ms = _ms_since(start_ms)
        tokens_out_est = _estimate_tokens(final_answer)

        meta = {
            "cache_hit": False,
            "latency_ms": latency_ms,
            "tokens_in_est": tokens_in_est,
            "tokens_out_est": tokens_out_est,
            "monthly_used_est": used,
            "model": BEDROCK_MODEL_ID,
        }
        answer_key_written = _write_answer_cache(job_id, ph, prompt_norm, final_answer, {**chunk_meta, **mr_stats}, meta)

        return _json(
            200,
            {
                "job_id": job_id,
                "status": "SUCCEEDED",
                "results_key": results_key,
                "answer_key": answer_key_written,
                "cache": {"hit": False},
                "chunking": {**chunk_meta, **mr_stats},
                "meta": meta,
                "response": final_answer,
            },
        )

    # Otherwise: check Textract job status
    first = textract.get_document_text_detection(JobId=job_id)
    job_status = first.get("JobStatus", "UNKNOWN")

    if job_status == "IN_PROGRESS":
        return _json(200, {"job_id": job_id, "status": "IN_PROGRESS", "results_key": results_key})

    if job_status != "SUCCEEDED":
        return _json(
            200,
            {"job_id": job_id, "status": job_status, "results_key": results_key, "detail": first.get("StatusMessage", "")},
        )

    # Pagination persistence
    state = _s3_get_json_if_exists(state_key) or {}
    next_token = state.get("next_token")
    pages_collected = int(state.get("pages_collected", 0))
    partial_text = _s3_get_text_if_exists(partial_key) or ""

    MAX_TOTAL_TEXT_CHARS = 500_000
    SOFT_TIME_BUDGET_MS = 22_000
    start_ms = _now_ms()

    def append_page(text_accum: str, page_text: str, page_num: int) -> str:
        addition = f"\n\n--- PAGE {page_num} ---\n\n{page_text.strip()}"
        if len(text_accum) + len(addition) > MAX_TOTAL_TEXT_CHARS:
            return text_accum
        return text_accum + addition

    # Determine where to start:
    # - If we've never collected anything, process `first` as page 1.
    # - If we have collected pages already, we MUST have a next_token to continue; otherwise don't re-fetch page 1.
    if pages_collected == 0:
        page_text = collect_text_from_get_document_text_detection(first)
        pages_collected = 1
        partial_text = append_page(partial_text, page_text, pages_collected)
        next_token = first.get("NextToken")
    else:
        if not next_token:
            # State says we already collected pages but there's no token to continue safely.
            # Treat as a state issue instead of duplicating page 1 repeatedly.
            return _json(
                409,
                {
                    "job_id": job_id,
                    "status": "STATE_ERROR",
                    "message": "Missing next_token for continued pagination. Cannot safely continue without duplicating pages.",
                    "pages_collected": pages_collected,
                    "partial_key": partial_key,
                    "state_key": state_key,
                },
            )

    # Continue pagination while time remains and next_token exists
    while True:
        if _now_ms() - start_ms > SOFT_TIME_BUDGET_MS or _remaining_ms(context) < 7_000:
            break
        if len(partial_text) >= MAX_TOTAL_TEXT_CHARS:
            break
        if not next_token:
            break

        resp = textract.get_document_text_detection(JobId=job_id, NextToken=next_token)
        page_text = collect_text_from_get_document_text_detection(resp)
        pages_collected += 1
        partial_text = append_page(partial_text, page_text, pages_collected)
        next_token = resp.get("NextToken")

    partial_text = _normalize_whitespace(partial_text)
    _s3_put_text(partial_key, partial_text)
    _s3_put_json(
        state_key,
        {"job_id": job_id, "pages_collected": pages_collected, "next_token": next_token, "updated_at": dt.datetime.utcnow().isoformat()},
    )

    if next_token:
        return _json(
            200,
            {
                "job_id": job_id,
                "status": "EXTRACTING",
                "results_key": results_key,
                "partial_key": partial_key,
                "pages_collected": pages_collected,
                "text_chars": len(partial_text),
                "message": "Extraction continuing. Call /status again.",
            },
        )

    # Finalize extracted.txt
    stitched_text = partial_text
    _s3_put_text(results_key, stitched_text)
    _s3_put_json(
        state_key,
        {
            "job_id": job_id,
            "pages_collected": pages_collected,
            "next_token": None,
            "complete": True,
            "finalized_at": dt.datetime.utcnow().isoformat(),
            "results_key": results_key,
        },
    )

    if not run_bedrock:
        return _json(
            200,
            {"job_id": job_id, "status": "SUCCEEDED", "results_key": results_key, "pages_collected": pages_collected, "text_chars": len(stitched_text)},
        )

    # Cache check again
    cached = _s3_get_json_if_exists(answer_key)
    if cached is not None:
        return _json(
            200,
            {
                "job_id": job_id,
                "status": "SUCCEEDED",
                "results_key": results_key,
                "answer_key": answer_key,
                "cache": {"hit": True},
                "response": cached.get("response", ""),
                "meta": cached.get("meta", {}),
            },
        )

    chunk_meta = _ensure_chunk_cache(job_id, stitched_text)

    tokens_in_est = _estimate_tokens_pair(prompt_norm, stitched_text)
    limit = _tier_limit(tier)
    ok, used = _update_usage_and_check_atomic(tenant_id, user_id, _month_key(), tokens_in_est, limit)
    if not ok:
        return _json(429, {"error": "Monthly token quota exceeded", "tier": tier, "used": used, "limit": limit})

    t0 = _now_ms()
    final_answer, mr_stats = _summarize_map_reduce(prompt_norm, schema, stitched_text, context, max_tokens_final=max_tokens)
    latency_ms = _ms_since(t0)
    tokens_out_est = _estimate_tokens(final_answer)

    meta = {
        "cache_hit": False,
        "latency_ms": latency_ms,
        "tokens_in_est": tokens_in_est,
        "tokens_out_est": tokens_out_est,
        "monthly_used_est": used,
        "model": BEDROCK_MODEL_ID,
    }
    answer_key_written = _write_answer_cache(job_id, ph, prompt_norm, final_answer, {**chunk_meta, **mr_stats}, meta)

    return _json(
        200,
        {
            "job_id": job_id,
            "status": "SUCCEEDED",
            "results_key": results_key,
            "answer_key": answer_key_written,
            "cache": {"hit": False},
            "chunking": {**chunk_meta, **mr_stats},
            "meta": meta,
            "response": final_answer,
        },
    )


# ---------- Router ----------
def _get_path(event: Dict[str, Any]) -> str:
    # HTTP API (v2)
    p = (event.get("requestContext") or {}).get("http", {}).get("path")
    if p:
        return p
    # REST API (v1)
    p = event.get("path")
    if p:
        return p
    return ""

def _as_bool(v, default=True):
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "t", "yes", "y", "on")
    return default

def lambda_handler(event, context):
    path = _get_path(event)
    body = _parse_json_body(event)
    action = body.get("action")

    print(f'event: "{event}"')
    print(f'path: "{path}"')
    print(f'body: "{body}"')
    print(f'action: "{action}"')
    print("AWS_REGION env:", os.environ.get("AWS_REGION"), os.environ.get("AWS_DEFAULT_REGION"))
    print("Session region:", boto3.session.Session().region_name)
    print("BEDROCK_MODEL_ID:", BEDROCK_MODEL_ID)

    if path.endswith("/upload-url") or "upload-url" == action:
        print("create_upload_url")
        return create_upload_url(event)
    if path.endswith("/invoke") or "invoke" == action:
        print("invoke")
        return process_document(event, context)
    if path.endswith("/status") or "status" == action:
        print("status")
        return status(event, context)

    return _json(404, {"error": "Not found", "path": path})