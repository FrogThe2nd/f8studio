# System Architecture

A Multi-process distributed multi-media data processing system. Embodied Intelligent platform.

``` mermaid
---
config:
  layout: dagre
  look: neo
  theme: mc
---
flowchart TB
 subgraph DS["Data Source"]
        MS["MS"]
        EIS["EIS"]
  end
 subgraph MS["Multi-media Sources"]
        VP["ImGUI Video Player"]
        SC["Screen Capture"]
        AC["Audio Capture"]
  end
 subgraph ALGO["Algorithm Bank"]
        PT["Human Pose Services"]
        OT["OpenCV Kit"]
        VSA["Audio Analysis Services"]
        AAT["ONNX (GPU) Models"]
  end
 subgraph PROCESSOR["Processing Hub"]
        SPN["PyEngineService"]
        PSN["PyExprService"]
        AIN["PyScriptService"]
  end
 subgraph EIS["External Data Streams"]
        NI["UnrealSkeletonStreamer"]
        LMP["UnitySkeletonStreamer"]
        n1["UnityLive2Studiotreamer"]
        n2["VMC Protocol (MMD)"]
  end
 subgraph STUDIO["F8Studio"]
        ITF["Data Visualizer"]
        n3["RunGraph Canvas"]
        n4["Code Editor"]
        n5["Service Manager"]
  end
 subgraph DEVICE["Device Interface"]
        n6["TCode (SR6)"]
        n7["Lovense API"]
        n8["Buttplug.io"]
        n9["TheHandy API"]
  end
    MS -- "Shared Memory<br>Zero-copy (SHM)" --> ALGO
    ALGO == NATS ==> PROCESSOR
    EIS == UDP ==> PROCESSOR
    PROCESSOR == WS / API / Series Port ==> DEVICE
    STUDIO <-. NATS .-> DS & ALGO & PROCESSOR

    style PROCESSOR fill:#BBDEFB
    style DEVICE fill:#C8E6C9
    style STUDIO fill:#FFCDD2
```