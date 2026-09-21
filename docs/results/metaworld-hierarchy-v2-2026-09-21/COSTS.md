# API cost estimate

This estimate uses provider-reported token usage from the latest supplemented v2 batch. Superseded timeout attempts are not included.

| Method | Requests | Input tokens | Output tokens | Rate | Estimated cost |
| --- | ---: | ---: | ---: | --- | ---: |
| Jev | 312 | 435,903 | 30,620 | $0.042/M input, $0/M output | $0.018308 |
| GPT-6 Astra | 312 | 316,469 | 8,812 | $10/M input, $50/M output | $3.605290 |

Notes:

- Jev reports both input and output usage fields, but TypeSafe published pricing bills Jev generated tokens at $0, so the estimate uses input tokens only.
- GPT-6 Astra is estimated with standard uncached text rates because the logs do not retain cached-token breakdowns.
- Tokenizers differ by provider; token counts are usage counters for each service, not equal units of computation.

Sources:

- Jev: https://docs.typesafe.ai/models
- GPT-6 Astra: https://developers.openai.com/api/docs/models/gpt-6-astra
