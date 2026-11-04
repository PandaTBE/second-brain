// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "SecondBrain",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "SecondBrain", targets: ["SecondBrain"]),
        .executable(name: "SecondBrainCoreChecks", targets: ["SecondBrainCoreChecks"]),
    ],
    targets: [
        .target(name: "SecondBrainCore"),
        .executableTarget(name: "SecondBrain", dependencies: ["SecondBrainCore"]),
        .executableTarget(name: "SecondBrainCoreChecks", dependencies: ["SecondBrainCore"], path: "Tests/SecondBrainCoreTests"),
    ],
    swiftLanguageModes: [.v5]
)
