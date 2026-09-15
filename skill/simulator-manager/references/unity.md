# Unity and a shared ADB server

Unity External Tools includes **Kill ADB server on exit** and **Kill external ADB instances**. Both are enabled by default according to [Unity 6000.3 documentation](https://docs.unity3d.com/6000.3/Documentation/Manual/android-external-tools-reference.html). These behaviors conflict with shared ADB ownership: Editor startup/exit can affect another task's active Android validation, including when the initiating task only runs host tests.

For participating Unity environments, coordinate an idle Editor window and disable both controls in Unity > Settings > External Tools > Android. They are user preferences affecting multiple projects. Record only their original values for reversal, verify no live Editor before changing them, and read back the result. Do not silently change SDK/JDK/NDK paths, Gradle behavior, signing settings or manager capacity/lease policy. Installation/bootstrap does not change Unity preferences automatically.

The observed macOS preference keys are `AndroidADBKillServerOnExit` and `AndroidADBKillExternalInstance` in `com.unity3d.UnityEditor5.x`; verify these against the installed Editor version before scripted changes. Avoid exporting the entire preferences file. A protocol release label difference (for example SDK 36 versus 37) does not establish an ADB client/server protocol mismatch.

Disabling automatic termination removes a known mechanism; it is not proof that every remote stop came from Unity or that the service is stable. Validate during subsequent normal supervised work using the configured ADB and assigned serial. Preserve app data and completed side-effectful steps. Do not restart Editors solely for diagnosis, kill the shared server, ignore unknown duplicate-AVD transports, or use a private server socket unsupported by the manager.

Unity failure blocks can contain a full Environment Variables dump. Keep raw logs local and provide sanitized copies with a whitelist of timestamps, known tool paths, exit codes and failure messages. Never publish raw environment values, tokens or credentials.
