import os

target = r"C:\Users\miche\OneDrive\Documentos\agent_squad\work\Depvision\EVOL-DEPVISION-ARQUITETURA\specs\solution-tree-and-folder-structure-design.md"

sec3 = """
---

## 3. Especificacao Detalhada por Camada e Modulo

### 3.1 Camada core/ (Microkernel e Orquestracao)

O diretorio core/ e o coracao do sistema, projetado segundo os principios de Clean Architecture. Ele nao depende de frameworks de UI (zero PySide6, zero Qt), nao depende da FastAPI e nao depende de implementacoes concretas de modelos de IA.

| Subdiretorio / Arquivo | Responsabilidade Unica | Principais Interfaces & Classes |
| :--- | :--- | :--- |
| core/app.py | Ponto de partida do ciclo de vida da engine. Inicializa container IoC, descobre plugins e registra shutdown gracioso. | class DepvisionCoreApplication |
| core/config.py | Configuracao imutavel tipada com suporte a .env, variaveis de ambiente e arquivos YAML de perfil. | class SystemSettings(BaseSettings) |
| core/container.py | Registrador de injecao de dependencias para desacoplamento total de componentes. | class DependencyContainer |
| core/bus/event_bus.py | Barramento assincrono pub/sub de baixa latencia em memoria para desacoplar emissao e consumo de eventos. | class EventBus, subscribe(), publish() |
| core/bus/events.py | Definicao de eventos imutaveis de dominio (ex: FrameRenderedEvent, VRAMAlertEvent). | class DomainEvent(ABC) |
| core/tasks/task_manager.py | Fila de execucao de tarefas assincronas com prioridades e controle cooperativo de cancelamento. | class TaskManager, class TaskQueue |
| core/memory/vram_arbiter.py | Gestor deterministico de orcamento de VRAM. Calcula espaco livre e aprova ou recusa carregamento de modelos. | class VRAMBudgetArbiter, request_allocation() |
| core/memory/cleaner.py | Executor do algoritmo de Debounced Idle VRAM Cleanup (libera buffers temporarios 300ms apos scrub da timeline). | class IdleVRAMCleaner, trigger_cleanup() |
| core/interfaces/plugin_base.py | Contrato formal de plugin. Obriga todo plugin a declarar metadados, schema de parametros, inputs e outputs. | class BasePlugin(ABC, Generic[TConfig]) |

#### Contrato Canonico de Plugin (core/interfaces/plugin_base.py):
```python
from abc import ABC, abstractmethod
from typing import Generic, TypeVar, Dict, Any
from pydantic import BaseModel

TConfig = TypeVar("TConfig", bound=BaseModel)

class PluginMetadata(BaseModel):
    id: str
    name: str
    version: str
    stage: int  # 1 a 11 no pipeline deterministico
    vram_estimate_mb: int
    supports_fp16: bool
    supports_tensorrt: bool

class BasePlugin(ABC, Generic[TConfig]):
    def __init__(self, config: TConfig):
        self.config = config
        self._is_loaded = False

    @property
    @abstractmethod
    def metadata(self) -> PluginMetadata:
        pass

    @abstractmethod
    async def load(self, device: str = "cuda") -> None:
        pass

    @abstractmethod
    async def execute(self, frame_context: Any) -> Any:
        pass

    @abstractmethod
    async def unload(self) -> None:
        pass
```

---

### 3.2 Camada gpu/ (Abstracao de Hardware, CUDA Streams & TensorRT)

O diretorio gpu/ isola toda a complexidade de compilacao, alocacao e inferencia de baixo nivel da NVIDIA.

| Subdiretorio / Arquivo | Responsabilidade Unica | Principais Interfaces & Classes |
| :--- | :--- | :--- |
| gpu/hardware.py | Consulta detalhada do driver NVIDIA via NVML/CUDA runtime (versao, arquitetura Blackwell/Ada/Ampere, VRAM livre). | class HardwareInspector, get_gpu_topology() |
| gpu/stream_pool.py | Implementa o Shared CUDA Stream Pool que mapeia threads assincronas para streams fixas, evitando explosao do alocador PyTorch. | class CUDAStreamPool, acquire_stream() |
| gpu/io_binding.py | Gestao de buffers de entrada e saida mantidos estritamente na VRAM via ONNX Runtime IO Binding. | class ZeroCopyIOBindingHelper |
| gpu/predictor/onnx_predictor.py | Invoca sessoes ONNXRuntime-GPU com flags de otimizacao e provedores CUDA/TensorRT configurados. | class ONNXPredictor(BasePredictor) |
| gpu/predictor/native_trt_predictor.py | Invoca engines nativas compiladas do TensorRT (IExecutionContext) com alocacao estatica de memoria. | class NativeTensorRTPredictor |
| gpu/builder/isolated_builder.py | Compilacao Isolada Multi-Processo: Executa a conversao ONNX -> TRT em subprocesso com timeout e captura de crash fatal. | class IsolatedEngineBuilder, compile_engine() |
| gpu/builder/shape_inference.py | Poda grafos simbolicos ONNX com dimensoes dinamicas antes da entrega ao TensorRT. | class SymbolicShapeInferencer |
| gpu/builder/fp16_allowlist.py | Garante que apenas os 38 modelos validados rodem em FP16, prevenindo falhas de segmentacao e erros de tracking. | FP16_SAFE_MODELS_REGISTRY: Set[str] |

---

### 3.3 Camada plugins/ (Modulos Autonomos de IA)

A pasta plugins/ implementa o padrao Microkernel. Cada subdiretorio agrupa uma familia de transformacoes funcionais correspondente aos 11 estagios identificados na auditoria:

```mermaid
flowchart LR
    E1["Estagio 1: Decode"] --> E2["Estagio 2: Deteccao"]
    E2 --> E3["Estagio 3: Align/Crop"]
    E3 --> E4["Estagio 4: Embedding"]
    E4 --> E5["Estagio 5: Swappers"]
    E5 --> E6["Estagio 6: Restorers Dual"]
    E6 --> E7["Estagio 7: Re-Aging"]
    E7 --> E8["Estagio 8: Recast/LivePortrait"]
    E8 --> E9["Estagio 9: Denoiser ReF-LDM"]
    E9 --> E10["Estagio 10: VR180 / Transform"]
    E10 --> E11["Estagio 11: Blend & Encode"]
```

1. **plugins/detection/ (Estagios 2 & 3)**:
   - Contem retinaface_plugin.py, scrfd_plugin.py, yolov8_face_plugin.py.
   - Gera caixas delimitadoras, landmarks (68 e 478 pontos) e recorta o rosto normalizado para 112x112 px.
2. **plugins/swappers/ (Estagio 5)**:
   - Contem inswapper_plugin.py, simswap_plugin.py, ghost_plugin.py, cscs_plugin.py, alphaface_plugin.py.
   - Aplica embeddings gerados no estagio 4 para projetar a face fonte na face alvo.
3. **plugins/restorers/ (Estagio 6)**:
   - Contem gfpgan_plugin.py, codeformer_plugin.py, restoreformer_plugin.py, gpen_plugin.py.
   - Suporta a arquitetura de Dual Restorers com interpolacao de peso linear (alpha de 0.0 a 1.0).
4. **plugins/editors/ (Estagios 7 & 8)**:
   - Contem face_reaging_plugin.py (transformacao continua de idade 5-channel ONNX).
   - Contem perform_recast_plugin.py (reenactment e transferencia de pose/expressao CVPR 2026).
   - Contem liveportrait_plugin.py (retargeting de olhos, boca e rotacao de cabeca).
5. **plugins/denoisers/ (Estagio 9)**:
   - Contem ref_ldm_denoiser.py (difusao latente condicionada para eliminar costuras e artefatos de iluminacao).
6. **plugins/projections/ (Estagio 10)**:
   - Contem vr180_plugin.py (renderizacao estereo lado a lado ou olho unico com correcao de distorcao angular).

---

### 3.4 Camada api/ (Exposicao FastAPI, WebSockets & Telemetria)

O diretorio api/ substitui completamente o transporte legado de pipes STDIN/STDOUT e qualquer resquicio de PySide6 por uma API moderna assincrona.

| Subdiretorio / Arquivo | Responsabilidade Unica | Protocolo / Transporte |
| :--- | :--- | :--- |
| api/server.py | Configura o app FastAPI com lifespans assincronos, CORS irrestrito para dev e autenticacao local. | ASGI (Uvicorn) |
| api/security.py | Valida cabecalho X-Depvision-Session-Token gerado efemeramente na inicializacao do desktop. | HTTP Middleware |
| api/routes/session_routes.py | Handshake inicial, ping/pong de saude e encerramento gracioso. | REST / GET / POST |
| api/routes/hardware_routes.py | Consulta status de VRAM e comuta providers (CUDA, TensorRT, FP16). | REST / GET / PUT |
| api/routes/library_routes.py | Upload e delecao de faces fonte (DropFaces) e midias alvo (DropFiles). | REST / Multipart |
| api/routes/pipeline_routes.py | Ativacao de plugins e atualizacao em tempo real dos 203 controles auditados. | REST / GET / PATCH |
| api/routes/render_routes.py | Submissao de lotes de renderizacao, pausa, retoma e cancelamento. | REST / POST |
| api/ws/video_stream.py | Transmissao de video quadro a quadro em tempo real com baixa latencia (<25ms). | WebSockets binario / JSON |
| api/sse/telemetry_stream.py | Streaming unidirecional continuo de VRAM livre, FPS real e temperatura da GPU. | Server-Sent Events (SSE) |

---

### 3.5 Camada desktop/ e frontend/ (Apresentacao & Supervisor)

1. **desktop/ (Electron Desktop Shell)**:
   - Atua exclusivamente como supervisor de processo.
   - desktop/main/sidecar_supervisor.ts:
     - Localiza porta TCP livre (port_finder.ts).
     - Gera chave criptografica efemera (UUIDv4/HMAC).
     - Executa o backend Python: python -m core.app --port 8000 --token <SESSION_TOKEN>.
     - Escuta eventos de saida e monitora heartbeat. Se o backend morrer, reinicia graciosamente ou exibe dialogo de diagnostico.
     - No encerramento da janela Electron, envia sinal de shutdown limpo (SIGTERM com timeout de 3s antes de SIGKILL).
2. **frontend/ (React 18 + Vite + TypeScript + TailwindCSS)**:
   - frontend/src/sdk/: Camada de abstracao que encapsula toda comunicacao com a API. A UI nunca executa chamadas diretas sem tipagem.
   - frontend/src/stores/: Gerenciamento de estado global via Zustand (imutavel, reativo e desacoplado).
   - Componentes visuais organizados com TailwindCSS e componentes de alta precisao para manipulacao de video.

---

## 4. Plano de Reconstrucao e Compilacao Modular por Pasta

Para garantir a viabilidade tecnica e permitir que a equipe de engenharia (06-software-engineer, 21-frontend-engineer, 22-backend-engineer) execute a migracao em etapas isoladas, tipadas e testaveis, o processo e dividido em 6 etapas cronologicas estritas:

```mermaid
graph TD
    S1["Etapa 1: Estrutura Base & Core Engine"] --> S2["Etapa 2: Subsistema de GPU & TensorRT"]
    S2 --> S3["Etapa 3: Plugins de IA & Services"]
    S3 --> S4["Etapa 4: Camada API FastAPI & Telemetria"]
    S4 --> S5["Etapa 5: Frontend SDK & Reativo"]
    S5 --> S6["Etapa 6: Electron Desktop Sidecar & E2E"]
```

### Etapa 1: Estrutura Base & Core Engine (core/)
- **Acao**: Criar os diretorios core/, core/bus/, core/tasks/, core/memory/, core/interfaces/.
- **Implementacao**:
  - Implementar PluginMetadata e BasePlugin em core/interfaces/plugin_base.py.
  - Implementar EventBus assincrono em core/bus/event_bus.py.
  - Implementar VRAMBudgetArbiter e IdleVRAMCleaner em core/memory/.
- **Criterio de Aceite / Teste Unitario**:
  - Testes unitarios em tests/unit/test_core_event_bus.py e test_vram_arbiter.py executando 100% no pytest sem dependencia de placa de video.

### Etapa 2: Subsistema de GPU & TensorRT (gpu/)
- **Acao**: Criar os diretorios gpu/, gpu/predictor/, gpu/builder/.
- **Implementacao**:
  - Extrair a logica industrial de compilacao isolada de VisoMaster-Fusion e empacotar em gpu/builder/isolated_builder.py.
  - Implementar o CUDAStreamPool e a fp16_allowlist.py.
  - Implementar a abstracao ONNXPredictor com fallback automatico de providers (TensorRT -> CUDA -> CPU).
- **Criterio de Aceite / Teste Unitario**:
  - Teste automatizado de inicializacao do builder em processo isolado; compilacao de um modelo leve de teste com timeout garantido.

### Etapa 3: Plugins de IA & Servicos (plugins/ e services/)
- **Acao**: Criar a taxonomia de plugins em plugins/ e os orquestradores em services/.
- **Implementacao**:
  - Criar plugins/registry.py com suporte a auto-descoberta baseada em subclasses de BasePlugin.
  - Migrar os detectores (retinaface, scrfd) conformando com o contrato BasePlugin.
  - Migrar os swappers (inswapper, simswap, ghost, cscs, alphaface).
  - Migrar os restorers (gfpgan, codeformer) e os editores (face_reaging, perform_recast, liveportrait).
  - Implementar services/pipeline_service.py executando a passagem deterministica de tensores pelos plugins ativos.
- **Criterio de Aceite / Teste Unitario**:
  - Execucao de pipeline sintetico com frame mock via tests/integration/test_pipeline_execution.py.

### Etapa 4: Camada de API FastAPI (api/)
- **Acao**: Criar os roteadores REST, canais WebSocket e endpoints SSE em api/.
- **Implementacao**:
  - Configurar Uvicorn assincrono em api/server.py.
  - Implementar as rotas mapeando os 203 controles de interface levantados na auditoria para modelos Pydantic v2.
  - Implementar api/ws/video_stream.py com pipeline assincrono de envio de frames codificados em JPEG/WebP.
  - Implementar api/sse/telemetry_stream.py conectado ao TelemetryEmitter do core.
- **Criterio de Aceite / Teste Unitario**:
  - Suite de testes da API via TestClient (FastAPI/Httpx) validando codigos de status HTTP 200/422 e streaming SSE.

### Etapa 5: Frontend SDK e Estado Reativo (frontend/)
- **Acao**: Reestruturar a pasta frontend/src/ com base no SDK unificado.
- **Implementacao**:
  - Implementar frontend/src/sdk/client.ts, ws_client.ts, sse_client.ts.
  - Criar as stores Zustand (pipeline_store.ts, hardware_store.ts, playback_store.ts).
  - Conectar os sliders e toggles existentes nos componentes React a nova store sem acoplamento a rotas espurias.
- **Criterio de Aceite / Teste Unitario**:
  - Teste de renderizacao de componentes e simulacao de atualizacao de telemetria via mock WebSocket/SSE.

### Etapa 6: Electron Desktop Shell & Validacao E2E (desktop/)
- **Acao**: Criar o supervisor de processo em desktop/main/sidecar_supervisor.ts.
- **Implementacao**:
  - Configurar a inicializacao do executavel Python em background com deteccao de porta livre.
  - Configurar contexto bridge seguro em desktop/preload/index.ts.
  - Executar teste de fumaca de ciclo de vida: inicializacao, handshake de sessao, renderizacao de 1 frame e encerramento limpo de processo.
- **Criterio de Aceite / Teste Unitario**:
  - Teste de ponta a ponta (tests/e2e/test_studio_flow.py) validando o encerramento do processo Python apos fechamento do Electron sem processos orfaos (zombie processes) no Windows.

---

## 5. Matriz de Rastreabilidade e Governanca de Arquivos

Para garantir integridade durante a migracao, a tabela abaixo documenta o destino exato de cada componente legado e cada inovacao do branch avancado:

| Arquivo Original (Legado ou VisoMaster-Fusion) | Novo Caminho Canonico na Solucao | Motivo Arquitetural / Refatoracao |
| :--- | :--- | :--- |
| D:\\Depvision\\app\\processors\\models_processor.py | core/memory/vram_arbiter.py + gpu/predictor/onnx_predictor.py | Separacao da gestao de VRAM da execucao de modelos ONNX. Zero codigo Qt. |
| D:\\Depvision\\app\\processors\\utils\\engine_builder.py | gpu/builder/isolated_builder.py | Execucao de compilacao TRT em subprocesso isolado com protecao contra crash fatal. |
| D:\\Depvision\\app\\processors\\utils\\tensorrt_predictor.py | gpu/predictor/native_trt_predictor.py | Tipagem estrita com buffers gerenciados e suporte a multiplos perfis dinamicos. |
| D:\\Depvision\\scripts\\VisoMaster-Fusion\\app\\processors\\face_reaging.py | plugins/editors/face_reaging_plugin.py | Encapsulamento como plugin modular herdando de BasePlugin. |
| D:\\Depvision\\scripts\\VisoMaster-Fusion\\app\\processors\\perform_recast.py | plugins/editors/perform_recast_plugin.py | Encapsulamento com Pydantic v2 para controle de pontos e pesos de expressao. |
| D:\\Depvision\\scripts\\VisoMaster-Fusion\\app\\processors\\face_denoiser.py | plugins/denoisers/ref_ldm_denoiser.py | Isolamento do modelo de difusao latente com gestao rigorosa de VRAM. |
| D:\\Depvision\\scripts\\VisoMaster-Fusion\\app\\processors\\alphaface\\ | plugins/swappers/alphaface_plugin.py | Padronizacao como plugin plug-and-play compativel com a biblioteca de faces. |
| D:\\Depvision\\app\\processors\\video_processor.py | services/pipeline_service.py | Desacoplamento de threads do PySide6; orquestracao assincrona pura. |
| D:\\Depvision\\python\\main_controller.py (88 KB) | api/routes/ + api/ws/ + core/tasks/ | Desmembramento do monolito em rotas granulares FastAPI e WebSockets. |
| D:\\Depvision\\electron\\main.js | desktop/main/sidecar_supervisor.ts | Migracao para TypeScript com supervisao robusta de processo e token HMAC. |

---

## 6. Conclusao & Proximos Passos de Arquitetura

O desenho da arvore de solucao e a estrutura minima de pastas aqui apresentados atendem plenamente as necessidades identificadas no diagnostico tecnico do Depvision:
- **Resolucao de Acoplamento**: Eliminacao definitiva da dependencia do PySide6/Qt no nucleo e na inferencia.
- **Resiliencia Industrial de GPU**: Adocao de isolamento de processo para compilacao TensorRT, pool compartilhado de CUDA Streams e gerenciador de VRAM por orcamentos.
- **Flexibilidade Hibrida**: Capacidade de operar como aplicativo desktop leve com sidecar local ou como servico distribuido em nuvem.
- **Modularidade Plug-and-Play**: Qualquer nova rede neural ou algoritmo de visao computacional pode ser adicionado criando-se apenas um novo arquivo em plugins/, sem tocar no nucleo ou no transporte.

Este documento constitui a especificacao tecnica canonica para aprovacao no portao **G2-Design** e habilitacao das etapas de scaffolding e implementacao pelos engenheiros especialistas do squad.
"""

with open(target, "a", encoding="utf-8") as f:
    f.write(sec3)

print("Appended successfully. Size:", os.path.getsize(target))
