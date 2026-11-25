// inspi-vision: for each image path on stdin, one JSON line {p, fp (base64 float32), n, txt}
import Foundation
import Vision
import ImageIO

func cg(_ path: String) -> CGImage? {
    guard let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil) else { return nil }
    let opts: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageAlways: true,
                                 kCGImageSourceThumbnailMaxPixelSize: 1024,
                                 kCGImageSourceCreateThumbnailWithTransform: true]
    return CGImageSourceCreateThumbnailAtIndex(src, 0, opts as CFDictionary)
}
let doOCR = !CommandLine.arguments.contains("--no-ocr")
while let line = readLine() {
    let path = line.trimmingCharacters(in: .whitespacesAndNewlines)
    if path.isEmpty { continue }
    var out: [String: Any] = ["p": path]
    if let img = cg(path) {
        let h = VNImageRequestHandler(cgImage: img, options: [:])
        let fp = VNGenerateImageFeaturePrintRequest()
        fp.revision = VNGenerateImageFeaturePrintRequestRevision2
        let ocr = VNRecognizeTextRequest()
        ocr.recognitionLevel = .accurate
        ocr.recognitionLanguages = ["fr-FR", "en-US"]
        ocr.usesLanguageCorrection = true
        do {
            try h.perform(doOCR ? [fp, ocr] : [fp])
            if let o = fp.results?.first {
                out["fp"] = o.data.base64EncodedString()
                out["n"] = o.elementCount
                out["rev"] = 2
                out["t"] = o.elementType == .float ? "f32" : "f64"
            }
            if doOCR {
                let words = (ocr.results ?? []).filter { $0.confidence > 0.4 }.compactMap { $0.topCandidates(1).first?.string }
                out["txt"] = words.joined(separator: " ")
            }
        } catch { out["err"] = "\(error)" }
    } else { out["err"] = "unreadable" }
    let d = try! JSONSerialization.data(withJSONObject: out)
    FileHandle.standardOutput.write(d); FileHandle.standardOutput.write("\n".data(using: .utf8)!)
}
