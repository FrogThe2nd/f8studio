# f8pyaudiofeat

Audio feature services for Feel8.

`f8.audiofeat.core` consumes Zenoh latest audio chunks through `audioKey` by default.
`audioShmName` remains as the explicit `legacy_shm` fallback for old graphs.

Service classes:
- `f8.audiofeat.core`
- `f8.audiofeat.rhythm`
