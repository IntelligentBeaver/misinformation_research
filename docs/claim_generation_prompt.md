I would like to contiune my work of generating claims based on the evidence given.

This is the prompt:
- Generate 3 different types of claims (Support, Refute, neutral) for each of the evidence given to you in text format.
- Provide the output json in text form here. 
- The claim_label can be support, refute, neutral. 
- From a given evidence with its evidence_id and other fields, duplicate it for keeping 3 different claims.
- The claim_id should follow a pattern: <claim_label>_<evidence_id>

For example this is the input:
  {
    "evidence_id": "ev_69c90c6c9d10",
    "exact_evidence_text": "The update incorporates new data sources and improved functionalities, including the use of artificial intelligence (AI).",
  },

- You should make three copies: one for each claim_label.
- The claim generated is allowed to have completely changed sturcutre and grammer and based on external knoweldge. Multiple senteces of claims are also possible regardless of the evidence length.
- The claims generated should not be a simple templated result such as "The evidence says...." or changing the evidence to negative or directly using evidence itself.
- Paraphrasing along with heavy modification of the evidence but keeping the meaning same is also allowed.
- The nuetral claims should not be 'The statement says...' or 'The passage says....' or anything simlar to this.

- The ouptut should be like this:
Evidences used: X
Evidences discarded: X (Reason: ....)
  {"evidence_id": "ev_1c276fa7f3d5","claim_id": "support_ev_1c276fa7f3d5","claim_label": "support","claim": "After treatment, side effects typically resolve within a few days and patients can usually return to work."},
  {"evidence_id": "ev_1c276fa7f3d5","claim_id": "refute_ev_1c276fa7f3d5","claim_label": "refute","claim": "Treatment usually causes long-term side effects that prevent patients from returning to normal activities."},
  {"evidence_id": "ev_1c276fa7f3d5","claim_id": "neutral_ev_1c276fa7f3d5","claim_label": "neutral","claim": "Recovery after treatment varies, and some individuals may experience temporary side effects."},


The refinements:
1. drop the source_file and exact_evidence_text field as it is redundant here. 
2. Also tell me the count of evidences you have used. Along with the discarded evidences.
3. If the evidences are too short to have any meaning whatsoever or has not enough context for claim generation, discard that evidence. 
4. only add neutral claim if there is not enough evidence to say true or false. Something like half evidence, if it supports only that then neutral.
5. Complete this task by generating claims for 50 evidences strictly in a single response from the evidences below.
6. Dont generate your own evidence or evidence id. Use only the evidence and evidence id provided and generate claims from them serially.
These are the 100 evidences:
