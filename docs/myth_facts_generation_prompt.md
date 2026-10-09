Start my work of generating claims(myth/facts) based on the evidence given. 
 
This is the prompt: 
- Generate 2 different types of claims (Support, Refute) for each of the evidence given to you in text format. 
- Provide the output json in text form here.  
- The claim_label can be support or refute.
- From a given evidence with its evidence_id, duplicate it for keeping 2 different claims. 
 
For example this is the input: 
  {
    "evidence_id": "ev_69c90c6c9d10",
    "exact_evidence_text": "The update incorporates new data sources and improved functionalities, including the use of artificial intelligence (AI).",
  },

- You should make two copies: one for each claim_label.
- The claim generated is allowed to have completely changed sturcutre and grammer and based on external knoweldge. Multiple senteces of claims are also possible regardless of the evidence length.
- The claims generated should not be a simple templated result such as "The evidence says...." or changing the evidence to negative or directly using evidence itself.
- Paraphrasing along with heavy modification of the evidence but keeping the meaning same is also allowed.
- The nuetral claims should not be 'The statement says...' or 'The passage says....' or anything simlar to this.

- The ouptut should be like this:
[ 
  { 
    "claim_label": "support", 
    "evidence_id": "ev_002a4d91ccdd", 
    "heading": "Fact: Hand sanitizers can be used often", 
    "text": "An alcohol-based sanitizer does not create antibiotic resistance. Unlike other antiseptics and antibiotics, pathogens (harmful germs) do not seem to develop resistance to alcohol-based sanitizers." 
  }, 
  { 
    "claim_label": "support", 
    "evidence_id": "ev_002a4d91c9jd", 
    "heading": "Fact: Alcohol-based sanitizers are safe for everyone to use", 
    "text": "Alcohols in the sanitizers have not been shown to create any relevant health issues. Little alcohol is absorbed into the skin, and most products contain an emollient to reduce skin dryness. Allergic contact dermatitis and bleaching of hand hair due to alcohol are very rare adverse effects. Accidental swallowing and intoxication have been described in rare cases." 
  }, 
  { 
    "claim_label": "refute", 
    "evidence_id": "ev_002a4d91cv5d", 
    "heading": "Fact: It's as easy to overdose on Suboxone as it is to overdose with other opioids.", 
    "text": "Reality: It is extremely difficult to overdose on Suboxone alone, compared to other opioids, because Suboxone is only a partial opiate receptor agonist, so there is a built-in ceiling effect - meaning there is a limit to how much the opioid receptors can be activated by Suboxone, so there isn't as great a risk of impaired breathing (which is what leads to death with an opioid overdose) compared with potent opiates such as heroin, oxycodone, or fentanyl. When people do overdose on Suboxone, it is almost always because they are mixing it with sedatives such as benzodiazepines, medicines that can additively impair breathing." 
  }, 
  { 
    "claim_label": "refute", 
    "evidence_id": "ev_002a4d91cc4d", 
    "heading": "Fact: Anti-inflammatory diets or certain foods (blueberries! kale! garlic!) prevent disease by suppressing inflammation", 
    "text": "While it's true that some foods and diets are healthier than others, it's not clear their benefits are due to reducing inflammation. Switching from a typical Western diet to an \"anti-inflammatory diet\" (such as the Mediterranean diet) improves health in multiple ways.\nEven if you could completely eliminate inflammation (which is not possible) you wouldn't want to. Quashing inflammation leaves you vulnerable to deadly infections. Your body couldn't effectively respond to allergens and toxins or recover from injuries. That said, it's important to adopt healthy lifestyle practices to keep chronic inflammation in check.\nFor additional advice about ways to reduce inflammation, check out Fighting Chronic Inflammation , a Special Health Report from Harvard Medical School." 
  }, 
  { 
    "claim_label": "refute", 
    "evidence_id": "ev_002a4d91h91q", 
    "heading": "Fact: I need a referral to see a physical therapist.", 
    "text": "FACT: Not usually, though some insurance plans - such as Medicare - do require a referral before covering the treatment. \"But many private insurers don't require that, and you can just walk into a PT center and get scheduled for a visit,\" she says. \"It's always a good idea to check with your insurance company first to make sure.\"" 
  } 
] 


The refinements: 
1. The claims (myth or facts) should be generated as questions or statements in random ratio.
2. Also tell me the count of evidences you have used. Along with the discarded evidences. 
3. If the evidences are too short to have any meaning whatsoever or has not enough context for claim generation, discard that evidence.  
4. The evidence is the "text" field and the claim is "heading" field. The heading field is for the claim that needs to be generated for each evidence.
5. Complete this task by generating claims for evidences strictly in a single response from the evidences below. 
6. Dont generate your own evidence. Use only the evidence provided and generate claims from them serially. 
7. Add "Myth: " and "Fact: " for myth claim and fact claim respectively.
These are the evidences: 