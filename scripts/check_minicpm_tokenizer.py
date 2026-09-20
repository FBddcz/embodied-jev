"""Check the pinned real tokenizer without downloading model weights."""
import json

from transformers import AutoTokenizer

from embodied_jev.policies import MODEL, REVISION


def main():
    tokenizer = AutoTokenizer.from_pretrained(MODEL, revision=REVISION)
    results = []
    for evidence in [{"recent_outcomes": []}, {"nested": [[]]}, {"object": [.4, -.17, .02]}]:
        prompt = tokenizer.apply_chat_template([{"role": "user", "content":
            "Choose an action. " + json.dumps(evidence) + "\nA. Lift\nB. Hold\nC. Release"}],
            tokenize=False, add_generation_prompt=True, enable_thinking=False)
        prefix = tokenizer.encode(prompt, add_special_tokens=False)
        ids = []
        for letter in "ABC":
            full = tokenizer.encode(prompt + letter, add_special_tokens=False)
            assert full[:-1] == prefix, "Candidate is not one token at this boundary"
            ids.append(full[-1])
        results.append({"evidence": evidence, "prompt_tokens": len(prefix), "candidate_token_ids": ids})
    print(json.dumps({"model": MODEL, "revision": REVISION, "tokenizer_boundary_checks": results,
                      "weights_loaded": False}, indent=2))


if __name__ == "__main__":
    main()
