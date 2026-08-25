# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: cloudflare-workers-ai.spec.ts >> Cloudflare UI — live stack @e2e >> Browse Workers AI models: live catalog loads, filters, and picks a model
- Location: tests/cloudflare-workers-ai.spec.ts:280:3

# Error details

```
Error: expect(received).toBeGreaterThan(expected)

Expected: > 0
Received:   0

Call Log:
- Test timeout of 30000ms exceeded
```

# Page snapshot

```yaml
- generic [ref=e1]:
  - generic [ref=e2]:
    - complementary [ref=e3]:
      - generic [ref=e4]:
        - generic [ref=e5]: SocialAuto
        - button "Collapse sidebar" [ref=e6] [cursor=pointer]
      - navigation "Main navigation" [ref=e9]:
        - link "Dashboard" [ref=e10]:
          - /url: /dashboard
        - link "Content" [ref=e17]:
          - /url: /content/new
        - link "Calendar" [ref=e22]:
          - /url: /calendar
        - link "Media Library" [ref=e26]:
          - /url: /media
        - link "Workflows" [ref=e32]:
          - /url: /workflows
        - link "Accounts" [ref=e36]:
          - /url: /accounts
        - link "Analytics" [ref=e43]:
          - /url: /analytics
        - link "Settings" [ref=e47]:
          - /url: /settings
      - generic [ref=e53]:
        - generic [ref=e54]: C
        - generic [ref=e56]:
          - paragraph [ref=e57]: Cloudflare E2E
          - paragraph [ref=e58]: cf-e2e-1787664218765-742684@example.com
        - button [ref=e59] [cursor=pointer]
    - generic [ref=e61]:
      - banner [ref=e62]:
        - generic [ref=e63]:
          - button "Search pages, actions... K" [ref=e64] [cursor=pointer]:
            - generic [ref=e68]: Search pages, actions...
            - generic [ref=e69]: K
          - generic [ref=e72]:
            - button "Switch to dark mode" [ref=e73] [cursor=pointer]
            - button "Notifications" [ref=e76] [cursor=pointer]
            - button "C" [ref=e80] [cursor=pointer]
      - main [ref=e83]:
        - generic [ref=e85]:
          - generic [ref=e86]:
            - heading "AI Providers" [level=1] [ref=e87]
            - paragraph [ref=e88]: Configure cloud inference APIs. Once saved, any AI feature can route to your chosen provider.
          - generic [ref=e89]:
            - generic [ref=e90]:
              - generic [ref=e92]:
                - generic [ref=e93]:
                  - generic [ref=e94]: 🦙
                  - generic [ref=e95]:
                    - heading "Local (Ollama)" [level=3] [ref=e96]
                    - paragraph [ref=e97]: Local GPU inference — no API key needed
                - generic [ref=e98]: Disabled
              - generic [ref=e100]:
                - generic [ref=e101]:
                  - generic [ref=e102]:
                    - text: Base URL
                    - textbox "http://localhost:11435" [ref=e104]
                  - generic [ref=e105]:
                    - text: Default Model
                    - combobox "llama3" [ref=e107]
                - generic [ref=e108]:
                  - generic [ref=e109] [cursor=pointer]:
                    - checkbox "Enabled" [ref=e110]
                    - generic [ref=e111]: Enabled
                  - generic [ref=e112] [cursor=pointer]:
                    - checkbox "Set as default" [ref=e113]
                    - generic [ref=e114]: Set as default
                - button "Save" [ref=e116] [cursor=pointer]
            - generic [ref=e119]:
              - generic [ref=e121]:
                - generic [ref=e122]:
                  - generic [ref=e123]: 🟢
                  - generic [ref=e124]:
                    - heading "NVIDIA Build" [level=3] [ref=e125]
                    - paragraph [ref=e126]: NVIDIA's cloud inference — free tier 1000 req/month
                - generic [ref=e127]: Disabled
              - generic [ref=e129]:
                - generic [ref=e130]:
                  - text: API Key
                  - generic [ref=e131]:
                    - textbox "Enter NVIDIA Build API key" [ref=e133]
                    - button [ref=e134] [cursor=pointer]
                - generic [ref=e138]:
                  - generic [ref=e139]:
                    - text: Base URL
                    - textbox "https://integrate.api.nvidia.com/v1" [ref=e141]
                  - generic [ref=e142]:
                    - text: Default Model
                    - combobox "meta/llama-3.1-70b-instruct" [ref=e144]
                - generic [ref=e145]:
                  - generic [ref=e146] [cursor=pointer]:
                    - checkbox "Enabled" [ref=e147]
                    - generic [ref=e148]: Enabled
                  - generic [ref=e149] [cursor=pointer]:
                    - checkbox "Set as default" [ref=e150]
                    - generic [ref=e151]: Set as default
                - button "Save" [ref=e153] [cursor=pointer]
            - generic [ref=e156]:
              - generic [ref=e158]:
                - generic [ref=e159]:
                  - generic [ref=e160]: 🤗
                  - generic [ref=e161]:
                    - heading "Hugging Face" [level=3] [ref=e162]
                    - paragraph [ref=e163]: HuggingFace Serverless Inference API
                - generic [ref=e164]: Disabled
              - generic [ref=e166]:
                - generic [ref=e167]:
                  - text: API Key
                  - generic [ref=e168]:
                    - textbox "Enter Hugging Face API key" [ref=e170]
                    - button [ref=e171] [cursor=pointer]
                - generic [ref=e175]:
                  - generic [ref=e176]:
                    - text: Base URL
                    - textbox "https://api-inference.huggingface.co/v1" [ref=e178]
                  - generic [ref=e179]:
                    - text: Default Model
                    - combobox "meta-llama/Llama-3.1-70B-Instruct" [ref=e181]
                - generic [ref=e182]:
                  - generic [ref=e183] [cursor=pointer]:
                    - checkbox "Enabled" [ref=e184]
                    - generic [ref=e185]: Enabled
                  - generic [ref=e186] [cursor=pointer]:
                    - checkbox "Set as default" [ref=e187]
                    - generic [ref=e188]: Set as default
                - button "Save" [ref=e190] [cursor=pointer]
            - generic [ref=e193]:
              - generic [ref=e195]:
                - generic [ref=e196]:
                  - generic [ref=e197]: ✨
                  - generic [ref=e198]:
                    - heading "OpenAI" [level=3] [ref=e199]
                    - paragraph [ref=e200]: OpenAI GPT-4o, o1
                - generic [ref=e201]: Disabled
              - generic [ref=e203]:
                - generic [ref=e204]:
                  - text: API Key
                  - generic [ref=e205]:
                    - textbox "Enter OpenAI API key" [ref=e207]
                    - button [ref=e208] [cursor=pointer]
                - generic [ref=e212]:
                  - generic [ref=e213]:
                    - text: Base URL
                    - textbox "https://api.openai.com/v1" [ref=e215]
                  - generic [ref=e216]:
                    - text: Default Model
                    - combobox "gpt-4o-mini" [ref=e218]
                - generic [ref=e219]:
                  - generic [ref=e220] [cursor=pointer]:
                    - checkbox "Enabled" [ref=e221]
                    - generic [ref=e222]: Enabled
                  - generic [ref=e223] [cursor=pointer]:
                    - checkbox "Set as default" [ref=e224]
                    - generic [ref=e225]: Set as default
                - button "Save" [ref=e227] [cursor=pointer]
            - generic [ref=e230]:
              - generic [ref=e232]:
                - generic [ref=e233]:
                  - generic [ref=e234]: ⚡
                  - generic [ref=e235]:
                    - heading "Groq" [level=3] [ref=e236]
                    - paragraph [ref=e237]: Ultra-fast inference — best Ollama drop-in for speed
                - generic [ref=e238]: Disabled
              - generic [ref=e240]:
                - generic [ref=e241]:
                  - text: API Key
                  - generic [ref=e242]:
                    - textbox "Enter Groq API key" [ref=e244]
                    - button [ref=e245] [cursor=pointer]
                - generic [ref=e249]:
                  - generic [ref=e250]:
                    - text: Base URL
                    - textbox "https://api.groq.com/openai/v1" [ref=e252]
                  - generic [ref=e253]:
                    - text: Default Model
                    - combobox "qwen/qwen3.6-27b" [ref=e255]
                - generic [ref=e256]:
                  - generic [ref=e257] [cursor=pointer]:
                    - checkbox "Enabled" [ref=e258]
                    - generic [ref=e259]: Enabled
                  - generic [ref=e260] [cursor=pointer]:
                    - checkbox "Set as default" [ref=e261]
                    - generic [ref=e262]: Set as default
                - button "Save" [ref=e264] [cursor=pointer]
            - generic [ref=e267]:
              - generic [ref=e269]:
                - generic [ref=e270]:
                  - generic [ref=e271]: 🔗
                  - generic [ref=e272]:
                    - heading "Together AI" [level=3] [ref=e273]
                    - paragraph [ref=e274]: Together AI — pay-per-token open model hosting
                - generic [ref=e275]: Disabled
              - generic [ref=e277]:
                - generic [ref=e278]:
                  - text: API Key
                  - generic [ref=e279]:
                    - textbox "Enter Together AI API key" [ref=e281]
                    - button [ref=e282] [cursor=pointer]
                - generic [ref=e286]:
                  - generic [ref=e287]:
                    - text: Base URL
                    - textbox "https://api.together.xyz/v1" [ref=e289]
                  - generic [ref=e290]:
                    - text: Default Model
                    - combobox "meta-llama/Meta-Llama-3.1-8B-Instruct-Turbo" [ref=e292]
                - generic [ref=e293]:
                  - generic [ref=e294] [cursor=pointer]:
                    - checkbox "Enabled" [ref=e295]
                    - generic [ref=e296]: Enabled
                  - generic [ref=e297] [cursor=pointer]:
                    - checkbox "Set as default" [ref=e298]
                    - generic [ref=e299]: Set as default
                - button "Save" [ref=e301] [cursor=pointer]
            - generic [ref=e304]:
              - generic [ref=e306]:
                - generic [ref=e307]:
                  - generic [ref=e308]: 🤖
                  - generic [ref=e309]:
                    - heading "NVIDIA FLUX.1-Kontext-dev" [level=3] [ref=e310]
                    - paragraph [ref=e311]: NVIDIA hosted FLUX.1-Kontext-dev image-to-image editing (no local GPU needed)
                - generic [ref=e312]: Disabled
              - generic [ref=e314]:
                - generic [ref=e315]:
                  - text: API Key
                  - generic [ref=e316]:
                    - textbox "Enter NVIDIA FLUX.1-Kontext-dev API key" [ref=e318]
                    - button [ref=e319] [cursor=pointer]
                - generic [ref=e323]:
                  - generic [ref=e324]:
                    - text: Base URL
                    - textbox "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-kontext-dev" [ref=e326]
                  - generic [ref=e327]:
                    - text: Default Model
                    - combobox "flux.1-kontext-dev" [ref=e329]
                - generic [ref=e330]:
                  - generic [ref=e331] [cursor=pointer]:
                    - checkbox "Enabled" [ref=e332]
                    - generic [ref=e333]: Enabled
                  - generic [ref=e334] [cursor=pointer]:
                    - checkbox "Set as default" [ref=e335]
                    - generic [ref=e336]: Set as default
                - button "Save" [ref=e338] [cursor=pointer]
            - generic [ref=e341]:
              - generic [ref=e343]:
                - generic [ref=e344]:
                  - generic [ref=e345]: 🤖
                  - generic [ref=e346]:
                    - heading "NVIDIA FLUX.1-dev" [level=3] [ref=e347]
                    - paragraph [ref=e348]: NVIDIA hosted FLUX.1-dev text-to-image generation (no local GPU needed)
                - generic [ref=e349]: Disabled
              - generic [ref=e351]:
                - generic [ref=e352]:
                  - text: API Key
                  - generic [ref=e353]:
                    - textbox "Enter NVIDIA FLUX.1-dev API key" [ref=e355]
                    - button [ref=e356] [cursor=pointer]
                - generic [ref=e360]:
                  - generic [ref=e361]:
                    - text: Base URL
                    - textbox "https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.1-dev" [ref=e363]
                  - generic [ref=e364]:
                    - text: Default Model
                    - combobox "flux.1-dev" [ref=e366]
                - generic [ref=e367]:
                  - generic [ref=e368] [cursor=pointer]:
                    - checkbox "Enabled" [ref=e369]
                    - generic [ref=e370]: Enabled
                  - generic [ref=e371] [cursor=pointer]:
                    - checkbox "Set as default" [ref=e372]
                    - generic [ref=e373]: Set as default
                - button "Save" [ref=e375] [cursor=pointer]
            - generic [ref=e378]:
              - generic [ref=e380]:
                - generic [ref=e381]:
                  - generic [ref=e382]: 🤖
                  - generic [ref=e383]:
                    - heading "Local Stable Diffusion 3.5" [level=3] [ref=e384]
                    - paragraph [ref=e385]: Local NVIDIA NIM for Stable Diffusion 3.5 (requires local GPU)
                - generic [ref=e386]: Disabled
              - generic [ref=e388]:
                - generic [ref=e389]:
                  - generic [ref=e390]:
                    - text: Base URL
                    - textbox "http://localhost:11435" [ref=e392]
                  - generic [ref=e393]:
                    - text: Default Model
                    - combobox "stable-diffusion-3.5-large" [ref=e395]
                - generic [ref=e396]:
                  - generic [ref=e397] [cursor=pointer]:
                    - checkbox "Enabled" [ref=e398]
                    - generic [ref=e399]: Enabled
                  - generic [ref=e400] [cursor=pointer]:
                    - checkbox "Set as default" [ref=e401]
                    - generic [ref=e402]: Set as default
                - button "Save" [ref=e404] [cursor=pointer]
            - generic [ref=e407]:
              - generic [ref=e409]:
                - generic [ref=e410]:
                  - generic [ref=e411]: ☁️
                  - generic [ref=e412]:
                    - heading "Cloudflare Workers AI" [level=3] [ref=e413]
                    - paragraph [ref=e414]: Cloudflare Workers AI — LLMs (Llama/Qwen/GLM/GPT-OSS), Whisper/Nova STT, FLUX images. Full live catalog browsable in this panel.
                - generic [ref=e415]: Disabled
              - generic [ref=e417]:
                - generic [ref=e418]:
                  - text: API Key
                  - generic [ref=e419]:
                    - textbox "Enter Cloudflare Workers AI API key" [ref=e421]
                    - button [ref=e422] [cursor=pointer]
                - generic [ref=e426]:
                  - generic [ref=e427]:
                    - text: Base URL
                    - 'textbox "https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/" [ref=e429]'
                  - generic [ref=e430]:
                    - text: Default Model
                    - combobox "@cf/meta/llama-3.1-8b-instruct" [ref=e432]
                - generic [ref=e433]:
                  - button "Hide model catalog" [active] [ref=e434] [cursor=pointer]
                  - generic [ref=e438]:
                    - generic [ref=e439]:
                      - textbox "Search models…" [ref=e441]
                      - combobox [ref=e442]:
                        - option "All tasks" [selected]
                        - option "Automatic Speech Recognition"
                        - option "Dumb Pipe"
                        - option "Image Classification"
                        - option "Image-to-Text"
                        - option "Text Classification"
                        - option "Text Embeddings"
                        - option "Text Generation"
                        - option "Text-to-Image"
                        - option "Text-to-Speech"
                        - option "Translation"
                    - paragraph [ref=e443]: 64 of 64 models — click one to set it as the Default Model
                    - generic [ref=e444]:
                      - button "@cf/pipecat-ai/smart-turn-v2 Dumb Pipe — An open source, community-driven, native audio turn detection model in 2nd version" [ref=e445] [cursor=pointer]:
                        - text: "@cf/pipecat-ai/smart-turn-v2"
                        - generic [ref=e446]: Dumb Pipe — An open source, community-driven, native audio turn detection model in 2nd version
                      - button "@cf/openai/gpt-oss-120b Text Generation — OpenAI’s open-weight models designed for powerful reasoning, agentic tasks, and versatile developer use cases – gpt-oss-120b is for production, general purpose, high reasoning use-cases." [ref=e447] [cursor=pointer]:
                        - text: "@cf/openai/gpt-oss-120b"
                        - generic [ref=e448]: Text Generation — OpenAI’s open-weight models designed for powerful reasoning, agentic tasks, and versatile developer use cases – gpt-oss-120b is for production, general purpose, high reasoning use-cases.
                      - button "@cf/baai/bge-m3 Text Embeddings — Multi-Functionality, Multi-Linguality, and Multi-Granularity embeddings model." [ref=e449] [cursor=pointer]:
                        - text: "@cf/baai/bge-m3"
                        - generic [ref=e450]: Text Embeddings — Multi-Functionality, Multi-Linguality, and Multi-Granularity embeddings model.
                      - button "@cf/huggingface/distilbert-sst-2-int8 Text Classification — Distilled BERT model that was finetuned on SST-2 for sentiment classification" [ref=e451] [cursor=pointer]:
                        - text: "@cf/huggingface/distilbert-sst-2-int8"
                        - generic [ref=e452]: Text Classification — Distilled BERT model that was finetuned on SST-2 for sentiment classification
                      - button "@cf/google/gemma-2b-it-lora Text Generation — This is a Gemma-2B base model that Cloudflare dedicates for inference with LoRA adapters. Gemma is a family of lightweight, state-of-the-art open models from Google, built from the same research and t" [ref=e453] [cursor=pointer]:
                        - text: "@cf/google/gemma-2b-it-lora"
                        - generic [ref=e454]: Text Generation — This is a Gemma-2B base model that Cloudflare dedicates for inference with LoRA adapters. Gemma is a family of lightweight, state-of-the-art open models from Google, built from the same research and t
                      - button "@cf/black-forest-labs/flux-2-klein-9b Text-to-Image — FLUX.2 [klein] 9B is a 9 billion parameter model that can generate images from text descriptions and supports multi-reference editing capabilities." [ref=e455] [cursor=pointer]:
                        - text: "@cf/black-forest-labs/flux-2-klein-9b"
                        - generic [ref=e456]: Text-to-Image — FLUX.2 [klein] 9B is a 9 billion parameter model that can generate images from text descriptions and supports multi-reference editing capabilities.
                      - button "@cf/meta/llama-3.2-3b-instruct Text Generation — The Llama 3.2 instruction-tuned text only models are optimized for multilingual dialogue use cases, including agentic retrieval and summarization tasks." [ref=e457] [cursor=pointer]:
                        - text: "@cf/meta/llama-3.2-3b-instruct"
                        - generic [ref=e458]: Text Generation — The Llama 3.2 instruction-tuned text only models are optimized for multilingual dialogue use cases, including agentic retrieval and summarization tasks.
                      - button "@cf/meta/llama-guard-3-8b Text Generation — Llama Guard 3 is a Llama-3.1-8B pretrained model, fine-tuned for content safety classification. Similar to previous versions, it can be used to classify content in both LLM inputs (prompt classificati" [ref=e459] [cursor=pointer]:
                        - text: "@cf/meta/llama-guard-3-8b"
                        - generic [ref=e460]: Text Generation — Llama Guard 3 is a Llama-3.1-8B pretrained model, fine-tuned for content safety classification. Similar to previous versions, it can be used to classify content in both LLM inputs (prompt classificati
                      - button "@cf/qwen/qwen3-embedding-0.6b Text Embeddings — The Qwen3 Embedding model series is the latest proprietary model of the Qwen family, specifically designed for text embedding and ranking tasks." [ref=e461] [cursor=pointer]:
                        - text: "@cf/qwen/qwen3-embedding-0.6b"
                        - generic [ref=e462]: Text Embeddings — The Qwen3 Embedding model series is the latest proprietary model of the Qwen family, specifically designed for text embedding and ranking tasks.
                      - button "@cf/myshell-ai/melotts Text-to-Speech — MeloTTS is a high-quality multi-lingual text-to-speech library by MyShell.ai." [ref=e463] [cursor=pointer]:
                        - text: "@cf/myshell-ai/melotts"
                        - generic [ref=e464]: Text-to-Speech — MeloTTS is a high-quality multi-lingual text-to-speech library by MyShell.ai.
                      - button "@cf/mistral/mistral-7b-instruct-v0.2-lora Text Generation — The Mistral-7B-Instruct-v0.2 Large Language Model (LLM) is an instruct fine-tuned version of the Mistral-7B-v0.2." [ref=e465] [cursor=pointer]:
                        - text: "@cf/mistral/mistral-7b-instruct-v0.2-lora"
                        - generic [ref=e466]: Text Generation — The Mistral-7B-Instruct-v0.2 Large Language Model (LLM) is an instruct fine-tuned version of the Mistral-7B-v0.2.
                      - button "@cf/deepgram/aura-2-es Text-to-Speech — Aura-2 is a context-aware text-to-speech (TTS) model that applies natural pacing, expressiveness, and fillers based on the context of the provided text. The quality of your text input directly impacts" [ref=e467] [cursor=pointer]:
                        - text: "@cf/deepgram/aura-2-es"
                        - generic [ref=e468]: Text-to-Speech — Aura-2 is a context-aware text-to-speech (TTS) model that applies natural pacing, expressiveness, and fillers based on the context of the provided text. The quality of your text input directly impacts
                      - button "@cf/moonshotai/kimi-k2.7-code Text Generation — Kimi K2.7 is a frontier-scale open-source 1T parameter model with a 262.1k context window, multi-turn tool calling, vision inputs, and structured outputs for agentic workloads." [ref=e469] [cursor=pointer]:
                        - text: "@cf/moonshotai/kimi-k2.7-code"
                        - generic [ref=e470]: Text Generation — Kimi K2.7 is a frontier-scale open-source 1T parameter model with a 262.1k context window, multi-turn tool calling, vision inputs, and structured outputs for agentic workloads.
                      - button "@cf/openai/whisper Automatic Speech Recognition — Whisper is a general-purpose speech recognition model. It is trained on a large dataset of diverse audio and is also a multitasking model that can perform multilingual speech recognition, speech trans" [ref=e471] [cursor=pointer]:
                        - text: "@cf/openai/whisper"
                        - generic [ref=e472]: Automatic Speech Recognition — Whisper is a general-purpose speech recognition model. It is trained on a large dataset of diverse audio and is also a multitasking model that can perform multilingual speech recognition, speech trans
                      - button "@cf/pfnet/plamo-embedding-1b Text Embeddings — PLaMo-Embedding-1B is a Japanese text embedding model developed by Preferred Networks, Inc. It can convert Japanese text input into numerical vectors and can be used for a wide range of applications," [ref=e473] [cursor=pointer]:
                        - text: "@cf/pfnet/plamo-embedding-1b"
                        - generic [ref=e474]: Text Embeddings — PLaMo-Embedding-1B is a Japanese text embedding model developed by Preferred Networks, Inc. It can convert Japanese text input into numerical vectors and can be used for a wide range of applications,
                      - button "@cf/llava-hf/llava-1.5-7b-hf Image-to-Text — LLaVA is an open-source chatbot trained by fine-tuning LLaMA/Vicuna on GPT-generated multimodal instruction-following data. It is an auto-regressive language model, based on the transformer architectu" [ref=e475] [cursor=pointer]:
                        - text: "@cf/llava-hf/llava-1.5-7b-hf"
                        - generic [ref=e476]: Image-to-Text — LLaVA is an open-source chatbot trained by fine-tuning LLaMA/Vicuna on GPT-generated multimodal instruction-following data. It is an auto-regressive language model, based on the transformer architectu
                      - button "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b Text Generation — DeepSeek-R1-Distill-Qwen-32B is a model distilled from DeepSeek-R1 based on Qwen2.5. It outperforms OpenAI-o1-mini across various benchmarks, achieving new state-of-the-art results for dense models." [ref=e477] [cursor=pointer]:
                        - text: "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b"
                        - generic [ref=e478]: Text Generation — DeepSeek-R1-Distill-Qwen-32B is a model distilled from DeepSeek-R1 based on Qwen2.5. It outperforms OpenAI-o1-mini across various benchmarks, achieving new state-of-the-art results for dense models.
                      - button "@cf/runwayml/stable-diffusion-v1-5-inpainting Text-to-Image — Stable Diffusion Inpainting is a latent text-to-image diffusion model capable of generating photo-realistic images given any text input, with the extra capability of inpainting the pictures by using a" [ref=e479] [cursor=pointer]:
                        - text: "@cf/runwayml/stable-diffusion-v1-5-inpainting"
                        - generic [ref=e480]: Text-to-Image — Stable Diffusion Inpainting is a latent text-to-image diffusion model capable of generating photo-realistic images given any text input, with the extra capability of inpainting the pictures by using a
                      - button "@cf/deepgram/flux Automatic Speech Recognition — Flux is the first conversational speech recognition model built specifically for voice agents." [ref=e481] [cursor=pointer]:
                        - text: "@cf/deepgram/flux"
                        - generic [ref=e482]: Automatic Speech Recognition — Flux is the first conversational speech recognition model built specifically for voice agents.
                      - button "@cf/deepgram/nova-3 Automatic Speech Recognition — Transcribe audio using Deepgram’s speech-to-text model" [ref=e483] [cursor=pointer]:
                        - text: "@cf/deepgram/nova-3"
                        - generic [ref=e484]: Automatic Speech Recognition — Transcribe audio using Deepgram’s speech-to-text model
                      - button "@cf/black-forest-labs/flux-1-schnell Text-to-Image — FLUX.1 [schnell] is a 12 billion parameter rectified flow transformer capable of generating images from text descriptions." [ref=e485] [cursor=pointer]:
                        - text: "@cf/black-forest-labs/flux-1-schnell"
                        - generic [ref=e486]: Text-to-Image — FLUX.1 [schnell] is a 12 billion parameter rectified flow transformer capable of generating images from text descriptions.
                      - button "@cf/meta/llama-3.1-8b-instruct-fp8 Text Generation — Llama 3.1 8B quantized to FP8 precision" [ref=e487] [cursor=pointer]:
                        - text: "@cf/meta/llama-3.1-8b-instruct-fp8"
                        - generic [ref=e488]: Text Generation — Llama 3.1 8B quantized to FP8 precision
                      - button "@cf/meta/llama-3.2-1b-instruct Text Generation — The Llama 3.2 instruction-tuned text only models are optimized for multilingual dialogue use cases, including agentic retrieval and summarization tasks." [ref=e489] [cursor=pointer]:
                        - text: "@cf/meta/llama-3.2-1b-instruct"
                        - generic [ref=e490]: Text Generation — The Llama 3.2 instruction-tuned text only models are optimized for multilingual dialogue use cases, including agentic retrieval and summarization tasks.
                      - button "@cf/moonshotai/kimi-k2.6 Text Generation — Kimi K2.6 is a frontier-scale open-source 1T parameter model with a 262.1k context window, multi-turn tool calling, vision inputs, and structured outputs for agentic workloads." [ref=e491] [cursor=pointer]:
                        - text: "@cf/moonshotai/kimi-k2.6"
                        - generic [ref=e492]: Text Generation — Kimi K2.6 is a frontier-scale open-source 1T parameter model with a 262.1k context window, multi-turn tool calling, vision inputs, and structured outputs for agentic workloads.
                      - button "@cf/zai-org/glm-4.7-flash Text Generation — GLM-4.7-Flash is a fast and efficient multilingual text generation model with a 131,072 token context window. Optimized for dialogue, instruction-following, and multi-turn tool calling across 100+ lan" [ref=e493] [cursor=pointer]:
                        - text: "@cf/zai-org/glm-4.7-flash"
                        - generic [ref=e494]: Text Generation — GLM-4.7-Flash is a fast and efficient multilingual text generation model with a 131,072 token context window. Optimized for dialogue, instruction-following, and multi-turn tool calling across 100+ lan
                      - button "@cf/microsoft/resnet-50 Image Classification — 50 layers deep image classification CNN trained on more than 1M images from ImageNet" [ref=e495] [cursor=pointer]:
                        - text: "@cf/microsoft/resnet-50"
                        - generic [ref=e496]: Image Classification — 50 layers deep image classification CNN trained on more than 1M images from ImageNet
                      - button "@cf/bytedance/stable-diffusion-xl-lightning Text-to-Image — SDXL-Lightning is a lightning-fast text-to-image generation model. It can generate high-quality 1024px images in a few steps." [ref=e497] [cursor=pointer]:
                        - text: "@cf/bytedance/stable-diffusion-xl-lightning"
                        - generic [ref=e498]: Text-to-Image — SDXL-Lightning is a lightning-fast text-to-image generation model. It can generate high-quality 1024px images in a few steps.
                      - button "@cf/meta-llama/llama-2-7b-chat-hf-lora Text Generation — This is a Llama2 base model that Cloudflare dedicated for inference with LoRA adapters. Llama 2 is a collection of pretrained and fine-tuned generative text models ranging in scale from 7 billion to 7" [ref=e499] [cursor=pointer]:
                        - text: "@cf/meta-llama/llama-2-7b-chat-hf-lora"
                        - generic [ref=e500]: Text Generation — This is a Llama2 base model that Cloudflare dedicated for inference with LoRA adapters. Llama 2 is a collection of pretrained and fine-tuned generative text models ranging in scale from 7 billion to 7
                      - button "@cf/meta/llama-3.3-70b-instruct-fp8-fast Text Generation — Llama 3.3 70B quantized to fp8 precision, optimized to be faster." [ref=e501] [cursor=pointer]:
                        - text: "@cf/meta/llama-3.3-70b-instruct-fp8-fast"
                        - generic [ref=e502]: Text Generation — Llama 3.3 70B quantized to fp8 precision, optimized to be faster.
                      - button "@cf/ibm-granite/granite-4.0-h-micro Text Generation — Granite 4.0 instruct models deliver strong performance across benchmarks, achieving industry-leading results in key agentic tasks like instruction following and function calling. These efficiencies ma" [ref=e503] [cursor=pointer]:
                        - text: "@cf/ibm-granite/granite-4.0-h-micro"
                        - generic [ref=e504]: Text Generation — Granite 4.0 instruct models deliver strong performance across benchmarks, achieving industry-leading results in key agentic tasks like instruction following and function calling. These efficiencies ma
                      - button "@cf/lykon/dreamshaper-8-lcm Text-to-Image — Stable Diffusion model that has been fine-tuned to be better at photorealism without sacrificing range." [ref=e505] [cursor=pointer]:
                        - text: "@cf/lykon/dreamshaper-8-lcm"
                        - generic [ref=e506]: Text-to-Image — Stable Diffusion model that has been fine-tuned to be better at photorealism without sacrificing range.
                      - button "@cf/deepseek-ai/deepseek-v4-flash-0731 Text Generation — DeepSeek-V4-Flash-0731 is the official release of DeepSeek-V4-Flash, superseding the preview version, with substantially enhanced agentic capabilities." [ref=e507] [cursor=pointer]:
                        - text: "@cf/deepseek-ai/deepseek-v4-flash-0731"
                        - generic [ref=e508]: Text Generation — DeepSeek-V4-Flash-0731 is the official release of DeepSeek-V4-Flash, superseding the preview version, with substantially enhanced agentic capabilities.
                      - button "@cf/leonardo/phoenix-1.0 Text-to-Image — Phoenix 1.0 is a model by Leonardo.Ai that generates images with exceptional prompt adherence and coherent text." [ref=e509] [cursor=pointer]:
                        - text: "@cf/leonardo/phoenix-1.0"
                        - generic [ref=e510]: Text-to-Image — Phoenix 1.0 is a model by Leonardo.Ai that generates images with exceptional prompt adherence and coherent text.
                      - button "@cf/stabilityai/stable-diffusion-xl-base-1.0 Text-to-Image — Diffusion-based text-to-image generative model by Stability AI. Generates and modify images based on text prompts." [ref=e511] [cursor=pointer]:
                        - text: "@cf/stabilityai/stable-diffusion-xl-base-1.0"
                        - generic [ref=e512]: Text-to-Image — Diffusion-based text-to-image generative model by Stability AI. Generates and modify images based on text prompts.
                      - button "@cf/meta/m2m100-1.2b Translation — Multilingual encoder-decoder (seq-to-seq) model trained for Many-to-Many multilingual translation" [ref=e513] [cursor=pointer]:
                        - text: "@cf/meta/m2m100-1.2b"
                        - generic [ref=e514]: Translation — Multilingual encoder-decoder (seq-to-seq) model trained for Many-to-Many multilingual translation
                      - button "@cf/ai4bharat/indictrans2-en-indic-1B Translation — IndicTrans2 is the first open-source transformer-based multilingual NMT model that supports high-quality translations across all the 22 scheduled Indic languages" [ref=e515] [cursor=pointer]:
                        - text: "@cf/ai4bharat/indictrans2-en-indic-1B"
                        - generic [ref=e516]: Translation — IndicTrans2 is the first open-source transformer-based multilingual NMT model that supports high-quality translations across all the 22 scheduled Indic languages
                      - button "@cf/black-forest-labs/flux-2-klein-4b Text-to-Image — FLUX.2 [klein] is an ultra-fast, distilled image model. It unifies image generation and editing in a single model, delivering state-of-the-art quality enabling interactive workflows, real-time preview" [ref=e517] [cursor=pointer]:
                        - text: "@cf/black-forest-labs/flux-2-klein-4b"
                        - generic [ref=e518]: Text-to-Image — FLUX.2 [klein] is an ultra-fast, distilled image model. It unifies image generation and editing in a single model, delivering state-of-the-art quality enabling interactive workflows, real-time preview
                      - button "@cf/baai/bge-small-en-v1.5 Text Embeddings — BAAI general embedding (Small) model that transforms any given text into a 384-dimensional vector" [ref=e519] [cursor=pointer]:
                        - text: "@cf/baai/bge-small-en-v1.5"
                        - generic [ref=e520]: Text Embeddings — BAAI general embedding (Small) model that transforms any given text into a 384-dimensional vector
                      - button "@cf/qwen/qwen2.5-coder-32b-instruct Text Generation — Qwen2.5-Coder is the latest series of Code-Specific Qwen large language models (formerly known as CodeQwen). As of now, Qwen2.5-Coder has covered six mainstream model sizes, 0.5, 1.5, 3, 7, 14, 32 bil" [ref=e521] [cursor=pointer]:
                        - text: "@cf/qwen/qwen2.5-coder-32b-instruct"
                        - generic [ref=e522]: Text Generation — Qwen2.5-Coder is the latest series of Code-Specific Qwen large language models (formerly known as CodeQwen). As of now, Qwen2.5-Coder has covered six mainstream model sizes, 0.5, 1.5, 3, 7, 14, 32 bil
                      - button "@cf/zai-org/glm-5.2 Text Generation — Z.ai's flagship agentic coding model" [ref=e523] [cursor=pointer]:
                        - text: "@cf/zai-org/glm-5.2"
                        - generic [ref=e524]: Text Generation — Z.ai's flagship agentic coding model
                      - button "@cf/nvidia/nemotron-3-120b-a12b Text Generation — NVIDIA Nemotron 3 Super is a hybrid MoE model with leading accuracy for multi-agent applications and specialized agentic AI systems." [ref=e525] [cursor=pointer]:
                        - text: "@cf/nvidia/nemotron-3-120b-a12b"
                        - generic [ref=e526]: Text Generation — NVIDIA Nemotron 3 Super is a hybrid MoE model with leading accuracy for multi-agent applications and specialized agentic AI systems.
                      - button "@cf/baai/bge-base-en-v1.5 Text Embeddings — BAAI general embedding (Base) model that transforms any given text into a 768-dimensional vector" [ref=e527] [cursor=pointer]:
                        - text: "@cf/baai/bge-base-en-v1.5"
                        - generic [ref=e528]: Text Embeddings — BAAI general embedding (Base) model that transforms any given text into a 768-dimensional vector
                      - button "@cf/aisingapore/gemma-sea-lion-v4-27b-it Text Generation — SEA-LION stands for Southeast Asian Languages In One Network, which is a collection of Large Language Models (LLMs) which have been pretrained and instruct-tuned for the Southeast Asia (SEA) region." [ref=e529] [cursor=pointer]:
                        - text: "@cf/aisingapore/gemma-sea-lion-v4-27b-it"
                        - generic [ref=e530]: Text Generation — SEA-LION stands for Southeast Asian Languages In One Network, which is a collection of Large Language Models (LLMs) which have been pretrained and instruct-tuned for the Southeast Asia (SEA) region.
                      - button "@cf/qwen/qwen3-30b-a3b-fp8 Text Generation — Qwen3 is the latest generation of large language models in Qwen series, offering a comprehensive suite of dense and mixture-of-experts (MoE) models. Built upon extensive training, Qwen3 delivers groun" [ref=e531] [cursor=pointer]:
                        - text: "@cf/qwen/qwen3-30b-a3b-fp8"
                        - generic [ref=e532]: Text Generation — Qwen3 is the latest generation of large language models in Qwen series, offering a comprehensive suite of dense and mixture-of-experts (MoE) models. Built upon extensive training, Qwen3 delivers groun
                      - button "@cf/black-forest-labs/flux-2-dev Text-to-Image — FLUX.2 [dev] is an image model from Black Forest Labs where you can generate highly realistic and detailed images, with multi-reference support." [ref=e533] [cursor=pointer]:
                        - text: "@cf/black-forest-labs/flux-2-dev"
                        - generic [ref=e534]: Text-to-Image — FLUX.2 [dev] is an image model from Black Forest Labs where you can generate highly realistic and detailed images, with multi-reference support.
                      - button "@cf/google/gemma-7b-it-lora Text Generation — This is a Gemma-7B base model that Cloudflare dedicates for inference with LoRA adapters. Gemma is a family of lightweight, state-of-the-art open models from Google, built from the same research and" [ref=e535] [cursor=pointer]:
                        - text: "@cf/google/gemma-7b-it-lora"
                        - generic [ref=e536]: Text Generation — This is a Gemma-7B base model that Cloudflare dedicates for inference with LoRA adapters. Gemma is a family of lightweight, state-of-the-art open models from Google, built from the same research and
                      - button "@cf/google/gemma-4-26b-a4b-it Text Generation — Gemma 4 is Google's most intelligent family of open models, built from Gemini 3 research to maximize intelligence-per-parameter." [ref=e537] [cursor=pointer]:
                        - text: "@cf/google/gemma-4-26b-a4b-it"
                        - generic [ref=e538]: Text Generation — Gemma 4 is Google's most intelligent family of open models, built from Gemini 3 research to maximize intelligence-per-parameter.
                      - button "@cf/mistralai/mistral-small-3.1-24b-instruct Text Generation — Building upon Mistral Small 3 (2501), Mistral Small 3.1 (2503) adds state-of-the-art vision understanding and enhances long context capabilities up to 128k tokens without compromising text performance" [ref=e539] [cursor=pointer]:
                        - text: "@cf/mistralai/mistral-small-3.1-24b-instruct"
                        - generic [ref=e540]: Text Generation — Building upon Mistral Small 3 (2501), Mistral Small 3.1 (2503) adds state-of-the-art vision understanding and enhances long context capabilities up to 128k tokens without compromising text performance
                      - button "@cf/deepseek-ai/deepseek-v4-pro-0813 Text Generation — DeepSeek V4 Pro is a high-capability reasoning model from DeepSeek with a one million token context window, built for long-horizon agentic workflows and complex, multi-step problem-solving" [ref=e541] [cursor=pointer]:
                        - text: "@cf/deepseek-ai/deepseek-v4-pro-0813"
                        - generic [ref=e542]: Text Generation — DeepSeek V4 Pro is a high-capability reasoning model from DeepSeek with a one million token context window, built for long-horizon agentic workflows and complex, multi-step problem-solving
                      - button "@cf/meta/llama-3.2-11b-vision-instruct Text Generation — The Llama 3.2-Vision instruction-tuned models are optimized for visual recognition, image reasoning, captioning, and answering general questions about an image." [ref=e543] [cursor=pointer]:
                        - text: "@cf/meta/llama-3.2-11b-vision-instruct"
                        - generic [ref=e544]: Text Generation — The Llama 3.2-Vision instruction-tuned models are optimized for visual recognition, image reasoning, captioning, and answering general questions about an image.
                      - button "@cf/qwen/qwen3.8-27b Text Generation — Qwen 3.8 27B is a 27-billion-parameter instruction-tuned language model from Alibaba's Qwen family, designed for vision, efficient general-purpose text generation and agentic workloads." [ref=e545] [cursor=pointer]:
                        - text: "@cf/qwen/qwen3.8-27b"
                        - generic [ref=e546]: Text Generation — Qwen 3.8 27B is a 27-billion-parameter instruction-tuned language model from Alibaba's Qwen family, designed for vision, efficient general-purpose text generation and agentic workloads.
                      - button "@cf/openai/whisper-tiny-en Automatic Speech Recognition — Whisper is a pre-trained model for automatic speech recognition (ASR) and speech translation. Trained on 680k hours of labelled data, Whisper models demonstrate a strong ability to generalize to many" [ref=e547] [cursor=pointer]:
                        - text: "@cf/openai/whisper-tiny-en"
                        - generic [ref=e548]: Automatic Speech Recognition — Whisper is a pre-trained model for automatic speech recognition (ASR) and speech translation. Trained on 680k hours of labelled data, Whisper models demonstrate a strong ability to generalize to many
                      - button "@cf/openai/whisper-large-v3-turbo Automatic Speech Recognition — Whisper is a pre-trained model for automatic speech recognition (ASR) and speech translation." [ref=e549] [cursor=pointer]:
                        - text: "@cf/openai/whisper-large-v3-turbo"
                        - generic [ref=e550]: Automatic Speech Recognition — Whisper is a pre-trained model for automatic speech recognition (ASR) and speech translation.
                      - button "@cf/deepgram/aura-1 Text-to-Speech — Aura is a context-aware text-to-speech (TTS) model that applies natural pacing, expressiveness, and fillers based on the context of the provided text. The quality of your text input directly impacts t" [ref=e551] [cursor=pointer]:
                        - text: "@cf/deepgram/aura-1"
                        - generic [ref=e552]: Text-to-Speech — Aura is a context-aware text-to-speech (TTS) model that applies natural pacing, expressiveness, and fillers based on the context of the provided text. The quality of your text input directly impacts t
                      - button "@cf/runwayml/stable-diffusion-v1-5-img2img Text-to-Image — Stable Diffusion is a latent text-to-image diffusion model capable of generating photo-realistic images. Img2img generate a new image from an input image with Stable Diffusion." [ref=e553] [cursor=pointer]:
                        - text: "@cf/runwayml/stable-diffusion-v1-5-img2img"
                        - generic [ref=e554]: Text-to-Image — Stable Diffusion is a latent text-to-image diffusion model capable of generating photo-realistic images. Img2img generate a new image from an input image with Stable Diffusion.
                      - button "@cf/openai/gpt-oss-20b Text Generation — OpenAI’s open-weight models designed for powerful reasoning, agentic tasks, and versatile developer use cases – gpt-oss-20b is for lower latency, and local or specialized use-cases." [ref=e555] [cursor=pointer]:
                        - text: "@cf/openai/gpt-oss-20b"
                        - generic [ref=e556]: Text Generation — OpenAI’s open-weight models designed for powerful reasoning, agentic tasks, and versatile developer use cases – gpt-oss-20b is for lower latency, and local or specialized use-cases.
                      - button "@cf/google/embeddinggemma-300m Text Embeddings — EmbeddingGemma is a 300M parameter, state-of-the-art for its size, open embedding model from Google, built from Gemma 3 (with T5Gemma initialization) and the same research and technology used to creat" [ref=e557] [cursor=pointer]:
                        - text: "@cf/google/embeddinggemma-300m"
                        - generic [ref=e558]: Text Embeddings — EmbeddingGemma is a 300M parameter, state-of-the-art for its size, open embedding model from Google, built from Gemma 3 (with T5Gemma initialization) and the same research and technology used to creat
                      - button "@cf/baai/bge-reranker-base Text Classification — Different from embedding model, reranker uses question and document as input and directly output similarity instead of embedding. You can get a relevance score by inputting query and passage to the re" [ref=e559] [cursor=pointer]:
                        - text: "@cf/baai/bge-reranker-base"
                        - generic [ref=e560]: Text Classification — Different from embedding model, reranker uses question and document as input and directly output similarity instead of embedding. You can get a relevance score by inputting query and passage to the re
                      - button "@cf/moondream/moondream3.1-9B-A2B Image-to-Text — Moondream 3 is a fast, efficient 9B mixture-of-experts vision language model (2B active parameters) that delivers frontier-level visual reasoning for tasks like object detection, pointing, OCR, and st" [ref=e561] [cursor=pointer]:
                        - text: "@cf/moondream/moondream3.1-9B-A2B"
                        - generic [ref=e562]: Image-to-Text — Moondream 3 is a fast, efficient 9B mixture-of-experts vision language model (2B active parameters) that delivers frontier-level visual reasoning for tasks like object detection, pointing, OCR, and st
                      - button "@cf/leonardo/lucid-origin Text-to-Image — Lucid Origin from Leonardo.AI is their most adaptable and prompt-responsive model to date. Whether you're generating images with sharp graphic design, stunning full-HD renders, or highly specific crea" [ref=e563] [cursor=pointer]:
                        - text: "@cf/leonardo/lucid-origin"
                        - generic [ref=e564]: Text-to-Image — Lucid Origin from Leonardo.AI is their most adaptable and prompt-responsive model to date. Whether you're generating images with sharp graphic design, stunning full-HD renders, or highly specific crea
                      - button "@cf/meta/llama-4-scout-17b-16e-instruct Text Generation — Meta's Llama 4 Scout is a 17 billion parameter model with 16 experts that is natively multimodal. These models leverage a mixture-of-experts architecture to offer industry-leading performance in text" [ref=e565] [cursor=pointer]:
                        - text: "@cf/meta/llama-4-scout-17b-16e-instruct"
                        - generic [ref=e566]: Text Generation — Meta's Llama 4 Scout is a 17 billion parameter model with 16 experts that is natively multimodal. These models leverage a mixture-of-experts architecture to offer industry-leading performance in text
                      - button "@cf/qwen/qwq-32b Text Generation — QwQ is the reasoning model of the Qwen series. Compared with conventional instruction-tuned models, QwQ, which is capable of thinking and reasoning, can achieve significantly enhanced performance in d" [ref=e567] [cursor=pointer]:
                        - text: "@cf/qwen/qwq-32b"
                        - generic [ref=e568]: Text Generation — QwQ is the reasoning model of the Qwen series. Compared with conventional instruction-tuned models, QwQ, which is capable of thinking and reasoning, can achieve significantly enhanced performance in d
                      - button "@cf/baai/bge-large-en-v1.5 Text Embeddings — BAAI general embedding (Large) model that transforms any given text into a 1024-dimensional vector" [ref=e569] [cursor=pointer]:
                        - text: "@cf/baai/bge-large-en-v1.5"
                        - generic [ref=e570]: Text Embeddings — BAAI general embedding (Large) model that transforms any given text into a 1024-dimensional vector
                      - button "@cf/deepgram/aura-2-en Text-to-Speech — Aura-2 is a context-aware text-to-speech (TTS) model that applies natural pacing, expressiveness, and fillers based on the context of the provided text. The quality of your text input directly impacts" [ref=e571] [cursor=pointer]:
                        - text: "@cf/deepgram/aura-2-en"
                        - generic [ref=e572]: Text-to-Speech — Aura-2 is a context-aware text-to-speech (TTS) model that applies natural pacing, expressiveness, and fillers based on the context of the provided text. The quality of your text input directly impacts
                - generic [ref=e573]:
                  - generic [ref=e574] [cursor=pointer]:
                    - checkbox "Enabled" [ref=e575]
                    - generic [ref=e576]: Enabled
                  - generic [ref=e577] [cursor=pointer]:
                    - checkbox "Set as default" [ref=e578]
                    - generic [ref=e579]: Set as default
                - button "Save" [ref=e581] [cursor=pointer]
  - button "Open Tanstack query devtools" [ref=e654] [cursor=pointer]
  - button "Open Next.js Dev Tools" [ref=e728] [cursor=pointer]
  - alert [ref=e734]
```

# Test source

```ts
  189 |     expect(body.content).toEqual(expect.any(String))
  190 |     expect(body.content.length).toBeGreaterThan(0)
  191 |     expect(Array.isArray(body.hashtags)).toBe(true)
  192 |   }, { timeout: 45_000 })
  193 | 
  194 |   test('Workers AI batch submit → retrieve (queueRequest)', async ({ request }) => {
  195 |     const submitted = await request.post(`${API_V1}/ai/workers-ai/batch`, {
  196 |       headers: { ...headers(), 'Content-Type': 'application/json' },
  197 |       data: {
  198 |         model: CF_EMBED_MODEL,
  199 |         requests: [{ text: 'hello world', external_reference: 'e2e-batch-1' }],
  200 |       },
  201 |     })
  202 |     expect(submitted.status()).toBe(200)
  203 |     const sub = await submitted.json()
  204 |     expect(sub).toMatchObject({
  205 |       request_id: expect.any(String),
  206 |       status: expect.any(String),
  207 |       model: CF_EMBED_MODEL,
  208 |     })
  209 | 
  210 |     const retrieved = await request.post(`${API_V1}/ai/workers-ai/batch/retrieve`, {
  211 |       headers: { ...headers(), 'Content-Type': 'application/json' },
  212 |       data: { model: CF_EMBED_MODEL, request_id: sub.request_id },
  213 |     })
  214 |     expect(retrieved.status()).toBe(200)
  215 |     const ret = await retrieved.json()
  216 |     expect(ret.model).toBe(CF_EMBED_MODEL)
  217 |     expect('status' in ret).toBe(true)
  218 |     expect('responses' in ret).toBe(true)
  219 |   }, { timeout: 60_000 })
  220 | 
  221 |   test('transcribe via real Whisper returns 200 (transcript) or 422 (no speech)', async ({ request }) => {
  222 |     const wav = makeToneWav(1)
  223 |     const r = await request.post(`${API_V1}/ai/transcribe`, {
  224 |       headers: headers(),
  225 |       multipart: {
  226 |         file: { name: 'tone.wav', mimeType: 'audio/wav', buffer: wav },
  227 |         model: '@cf/openai/whisper',
  228 |       },
  229 |     })
  230 |     expect([200, 422]).toContain(r.status())
  231 |     const body = await r.json().catch(() => null)
  232 |     if (r.status() === 200) {
  233 |       expect(body.text).toEqual(expect.any(String))
  234 |     }
  235 |   }, { timeout: 60_000 })
  236 | })
  237 | 
  238 | 
  239 | 
  240 | 
  241 | // ─────────────────────────────────────────────────────────────────────────────
  242 | //  Real browser E2E (Next dev server on :3001 → live social-api backend)
  243 | // ─────────────────────────────────────────────────────────────────────────────
  244 | test.describe('Cloudflare UI — live stack @e2e', () => {
  245 |   test.describe.configure({ mode: 'serial' })
  246 | 
  247 |   test.beforeAll(async ({ request }) => {
  248 |     await registerAndLogin(request)
  249 |   })
  250 | 
  251 |   test.use({ baseURL: 'http://localhost:3001' })
  252 | 
  253 |   test.beforeEach(async ({ page }) => {
  254 |     // Inject auth tokens + tour-completed flag before any JS runs so dashboard
  255 |     // pages never redirect and the onboarding tour never blocks interaction.
  256 |     await page.addInitScript(
  257 |       ([a, r]) => {
  258 |         try {
  259 |           localStorage.setItem('access_token', a as string)
  260 |           localStorage.setItem('refresh_token', r as string)
  261 |           localStorage.setItem('tour_completed', 'true')
  262 |         } catch {
  263 |           /* localStorage unavailable — ignore */
  264 |         }
  265 |       },
  266 |       [accessToken, refreshToken],
  267 |     )
  268 |   })
  269 | 
  270 |   test('AI Providers page renders the Cloudflare card from the live catalog', async ({ page }) => {
  271 |     await page.goto('/settings/ai-providers')
  272 |     await expect(page).toHaveURL(/\/settings\/ai-providers/)
  273 |     const cfCard = page.locator('.bg-card', { hasText: 'Cloudflare Workers AI' }).first()
  274 |     await expect(cfCard).toBeVisible()
  275 |     await expect(cfCard.getByRole('button', { name: /browse workers ai models/i })).toBeVisible()
  276 |   })
  277 | 
  278 | 
  279 | 
  280 |   test('Browse Workers AI models: live catalog loads, filters, and picks a model', async ({ page }) => {
  281 |     await page.goto('/settings/ai-providers')
  282 |     const cfCard = page.locator('.bg-card', { hasText: 'Cloudflare Workers AI' }).first()
  283 | 
  284 |     // Toggle the live Workers AI model browser
  285 |     await cfCard.getByRole('button', { name: /browse workers ai models/i }).click()
  286 |     await expect(page.getByText(/Loading Workers AI catalog/)).toBeVisible()
  287 |     await expect
  288 |       .poll(async () => await cfCard.locator('button.font-mono').count(), { timeout: 30_000 })
> 289 |       .toBeGreaterThan(0)
      |        ^ Error: expect(received).toBeGreaterThan(expected)
  290 | 
  291 |     // Every rendered row is a real Workers AI model id
  292 |     const firstId = (await cfCard.locator('button.font-mono').first().textContent())?.trim()
  293 |     expect(firstId).toMatch(/^@cf\//)
  294 | 
  295 |     // Search filters the list
  296 |     await page.getByPlaceholder('Search models…').fill('whisper')
  297 |     await expect
  298 |       .poll(async () => {
  299 |         const rows = await cfCard.locator('button.font-mono').allTextContents()
  300 |         return rows.some((t) => t.toLowerCase().includes('whisper'))
  301 |       }, { timeout: 15_000 })
  302 |             .toBe(true)
  303 | 
  304 |     // Pick the first (filtered) model → its id lands in the Default Model input
  305 |     const picked = firstId
  306 |     await cfCard.locator('button.font-mono').first().click()
  307 |     const inputValues = await cfCard
  308 |       .locator('input')
  309 |       .evaluateAll((els) => els.map((e) => (e as HTMLInputElement).value))
  310 |     expect(inputValues).toContain(picked)
  311 |   }, { timeout: 60_000 })
  312 | 
  313 |   test('Save Cloudflare provider → "Provider saved" toast and persistence', async ({ page }) => {
  314 |     await page.goto('/settings/ai-providers')
  315 |     const cfCard = page.locator('.bg-card', { hasText: 'Cloudflare Workers AI' }).first()
  316 | 
  317 |     // "Enabled" is the first checkbox in the card's toggle row
  318 |     const enabledCheckbox = cfCard.getByRole('checkbox').first()
  319 |     if (!(await enabledCheckbox.isChecked())) await enabledCheckbox.check()
  320 |     const saveBtn = cfCard.getByRole('button', { name: /^save$/i })
  321 |     await expect(saveBtn).toBeVisible()
  322 |     await saveBtn.click()
  323 |     await expect(page.getByText('Provider saved')).toBeVisible({ timeout: 15_000 })
  324 |   }, { timeout: 60_000 })
  325 | 
  326 |     test('Test provider button fires a real Cloudflare connectivity check', async ({ page }) => {
  327 |     await page.goto('/settings/ai-providers')
  328 |     const cfCard = page.locator('.bg-card', { hasText: 'Cloudflare Workers AI' }).first()
  329 |     await cfCard.getByRole('button', { name: /^test$/i }).click()
  330 |     // Success toast reads: Connected! "<response…>"
  331 |     await expect(page.getByText(/connected!/i)).toBeVisible({ timeout: 45_000 })
  332 |   }, { timeout: 60_000 })
  333 | 
  334 |   test('VoiceRecorder uploads real (fake-device) audio to the live /ai/transcribe endpoint', async ({
  335 |     page,
  336 |     browserName,
  337 |   }) => {
  338 |         // Fake-device microphone capture is Chromium-only for the bundled browsers.
  339 |     test.skip(browserName !== 'chromium', 'fake audio device is Chromium-only')
  340 |     test.use({
  341 |       permissions: ['microphone'],
  342 |       launchOptions: {
  343 |         args: [
  344 |           '--use-fake-device-for-media-stream',
  345 |           '--use-fake-ui-for-media-stream',
  346 |           '--autoplay-policy=user-gesture-required',
  347 |         ],
  348 |       },
  349 |     })
  350 |     test.setTimeout(120_000)
  351 | 
  352 |     const transcribeResp = page.waitForResponse(
  353 |       (r) => r.url().includes('/api/v1/ai/transcribe'),
  354 |       { timeout: 60_000 },
  355 |     )
  356 |     await page.goto('/content/new')
  357 |     // Wait for the content editor to be ready (its placeholder is unique).
  358 |     await expect(page.getByPlaceholder('What do you want to share?')).toBeVisible({ timeout: 20_000 })
  359 | 
  360 |     // The recorder button carries an explicit aria-label.
  361 |     await page.getByRole('button', { name: 'Record and transcribe speech' }).click()
  362 |     // While recording the same button flips to "Stop recording"
  363 |     const stopBtn = page.getByRole('button', { name: 'Stop recording' })
  364 |     await expect(stopBtn).toBeVisible()
  365 |     // Capture a couple seconds of the fake-device tone.
  366 |     await page.waitForTimeout(3000)
  367 |     await stopBtn.click()
  368 | 
  369 |     const resp = await transcribeResp
  370 |     expect([200, 422]).toContain(resp.status())
  371 | 
  372 |     if (resp.status() === 200) {
  373 |       // Real Whisper output — transcript injected into the editor + success toast.
  374 |       await expect(page.getByText('Transcript added to your post')).toBeVisible({ timeout: 15_000 })
  375 |     } else {
  376 |       // Empty/inaudible tone → backend 422, frontend must NOT crash.
  377 |       await expect(page.getByText(/no speech detected|Transcription failed/i)).toBeVisible({ timeout: 15_000 })
  378 |     }
  379 |   }, { retry: 1 })
  380 | })
  381 | 
  382 | 
  383 | 
  384 | 
```