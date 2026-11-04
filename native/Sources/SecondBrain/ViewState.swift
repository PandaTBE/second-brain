import SwiftUI

// Select the public property wrapper; SDK 27 also exports a State macro whose plugin needs Xcode.
typealias ViewState<Value> = SwiftUI.State<Value>
