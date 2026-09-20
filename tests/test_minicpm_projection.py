"""Numerical check with tiny random Llama weights; not a model quality test."""
import pytest


def test_sparse_projection_matches_full_vocabulary(monkeypatch):
    torch = pytest.importorskip("torch")
    transformers = pytest.importorskip("transformers")
    from embodied_jev import policies
    torch.manual_seed(0)
    model = transformers.LlamaForCausalLM(transformers.LlamaConfig(
        vocab_size=16, hidden_size=16, intermediate_size=32, num_hidden_layers=1,
        num_attention_heads=2, num_key_value_heads=2, head_dim=8)).eval()
    class Tokenizer:
        def apply_chat_template(self, *args, **kwargs):
            return "prompt:"
        def encode(self, text, **kwargs):
            return [1, 2] + ({"A": [3], "B": [4]}.get(text[-1], []))
    monkeypatch.setattr(policies, "load_minicpm", lambda device: (Tokenizer(), model))
    monkeypatch.setenv("EMBODIED_MINICPM", "1")
    policy = policies.DecisionPolicy("minicpm")
    result = policy.choose({}, "Choose", {"lift": "Lift", "hold": "Hold"}, "lift", [])
    with torch.inference_mode():
        logits = model(input_ids=torch.tensor([[1, 2]])).logits[0, -1, [3, 4]]
        expected = torch.softmax(logits.float(), -1).tolist()
    assert list(result["probabilities"].values()) == pytest.approx(expected, abs=1e-7)
    assert result["model_call"]
