// macOS ImageCaptureCore transport for the Z6 II / Z7 II C1.70 profile.
// This process does not inspect or alter firmware. The Python profile owns
// model/version checks, backups, activation sequencing, and result logging.
#import <Foundation/Foundation.h>
#import <ImageCaptureCore/ImageCaptureCore.h>
#include <CoreFoundation/CFByteOrder.h>
#include <errno.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static double clockNow(void) {
    return NSProcessInfo.processInfo.systemUptime;
}

static void pump(double seconds) {
    NSDate *until = [NSDate dateWithTimeIntervalSinceNow:seconds];
    [[NSRunLoop mainRunLoop] runMode:NSDefaultRunLoopMode beforeDate:until];
}

static void emitJSON(NSDictionary *value) {
    NSError *error = nil;
    NSData *data = [NSJSONSerialization dataWithJSONObject:value options:0 error:&error];
    if (!data) {
        fprintf(stderr, "JSON serialization failed: %s\n", error.localizedDescription.UTF8String);
        return;
    }
    fwrite(data.bytes, 1, data.length, stdout);
    fputc('\n', stdout);
    fflush(stdout);
}

static NSDictionary *failure(NSString *message) {
    return @{ @"error": message ?: @"unknown transport error" };
}

static NSData *commandPacket(uint16_t opcode, NSArray<NSNumber *> *params, uint32_t transaction) {
    uint32_t length = CFSwapInt32HostToLittle((uint32_t)(12 + 4 * params.count));
    uint16_t type = CFSwapInt16HostToLittle(1);
    uint16_t code = CFSwapInt16HostToLittle(opcode);
    uint32_t tx = CFSwapInt32HostToLittle(transaction);
    NSMutableData *packet = [NSMutableData data];
    [packet appendBytes:&length length:4];
    [packet appendBytes:&type length:2];
    [packet appendBytes:&code length:2];
    [packet appendBytes:&tx length:4];
    for (NSNumber *number in params) {
        uint32_t value = CFSwapInt32HostToLittle(number.unsignedIntValue);
        [packet appendBytes:&value length:4];
    }
    return packet;
}

static NSDictionary *parseResponse(NSData *response) {
    if (response.length < 12 || (response.length - 12) % 4 != 0) {
        return failure(@"invalid PTP response length");
    }
    const uint8_t *bytes = response.bytes;
    uint32_t length = 0, tx = 0;
    uint16_t type = 0, code = 0;
    memcpy(&length, bytes, 4);
    memcpy(&type, bytes + 4, 2);
    memcpy(&code, bytes + 6, 2);
    memcpy(&tx, bytes + 8, 4);
    length = CFSwapInt32LittleToHost(length);
    type = CFSwapInt16LittleToHost(type);
    code = CFSwapInt16LittleToHost(code);
    tx = CFSwapInt32LittleToHost(tx);
    if (length != response.length || type != 3) {
        return failure(@"invalid PTP response container");
    }
    NSMutableArray<NSNumber *> *params = [NSMutableArray array];
    for (NSUInteger offset = 12; offset < response.length; offset += 4) {
        uint32_t value = 0;
        memcpy(&value, bytes + offset, 4);
        [params addObject:@(CFSwapInt32LittleToHost(value))];
    }
    return @{ @"code": @(code), @"params": params, @"response_tx": @(tx) };
}

static BOOL validNumbers(NSArray *params) {
    if (![params isKindOfClass:NSArray.class] || params.count > 5) return NO;
    for (id value in params) {
        if (![value isKindOfClass:NSNumber.class]) return NO;
        if ([value longLongValue] < 0 || [value unsignedLongLongValue] > UINT32_MAX) return NO;
    }
    return YES;
}

// Exact command allowlist for unlock_hdmi_raw_ii.py. No generic camera memory,
// reset, language, or firmware operations are reachable through this bridge.
static BOOL allowed(NSString *method, uint16_t opcode, NSArray<NSNumber *> *params, BOOL permitActivation) {
    if ([method isEqualToString:@"read"]) {
        return (opcode == 0x1001 && params.count == 0) ||
               (opcode == 0x94FF && [params isEqualToArray:@[@0xF02D, @0, @0x1FFFC, @0, @0]]);
    }
    if (![method isEqualToString:@"send"]) return NO;
    if (opcode == 0x944B) return params.count == 0;
    if (opcode == 0x94FE) return [params isEqualToArray:@[@0xF002, @0, @0, @0, @0]];
    if (opcode != 0x94FD) return NO;
    return [params isEqualToArray:@[@0xF001, @0, @0, @0, @0]] ||
           [params isEqualToArray:@[@0xC101, @0, @0, @0, @0]] ||
           [params isEqualToArray:@[@0xC051, @0, @0, @0, @0]] ||
           (permitActivation &&
            ([params isEqualToArray:@[@0xC102, @0x28CD7EE8, @0, @0, @0]] ||
             [params isEqualToArray:@[@0xC052, @1, @0, @0, @0]] ||
             [params isEqualToArray:@[@0xF030, @0, @0, @0, @0]]));
}

@interface NikonBridge : NSObject <ICDeviceBrowserDelegate, ICCameraDeviceDelegate>
@property(nonatomic, strong) ICDeviceBrowser *browser;
@property(nonatomic, strong) NSMutableArray<ICCameraDevice *> *cameras;
@property(nonatomic, strong) ICCameraDevice *camera;
@property(nonatomic) BOOL enumerated;
@property(nonatomic) BOOL opened;
@property(nonatomic) BOOL openComplete;
@property(nonatomic) BOOL closeComplete;
@property(nonatomic) BOOL removed;
@property(nonatomic) BOOL pending;
@property(nonatomic) BOOL indeterminate;
@property(nonatomic) BOOL permitActivation;
@property(nonatomic, strong) NSError *sessionError;
@property(nonatomic) uint32_t transaction;
- (void)startBrowser;
- (NSArray<NSDictionary *> *)deviceList;
- (NSString *)openDevice:(NSString *)deviceID;
- (NSDictionary *)transact:(NSDictionary *)request;
- (void)close;
@end

@implementation NikonBridge
- (instancetype)init {
    if ((self = [super init])) {
        self.cameras = [NSMutableArray array];
        self.transaction = 1;
    }
    return self;
}

- (void)deviceBrowser:(ICDeviceBrowser *)browser didAddDevice:(ICDevice *)device moreComing:(BOOL)more {
    if ([device isKindOfClass:ICCameraDevice.class] && device.usbVendorID == 0x04b0) {
        [self.cameras addObject:(ICCameraDevice *)device];
    }
    if (!more) self.enumerated = YES;
}

- (void)deviceBrowser:(ICDeviceBrowser *)browser didRemoveDevice:(ICDevice *)device moreGoing:(BOOL)more {
    if (device == self.camera) self.removed = YES;
    [self.cameras removeObject:(ICCameraDevice *)device];
}

- (void)deviceBrowserDidEnumerateLocalDevices:(ICDeviceBrowser *)browser {
    self.enumerated = YES;
}

- (void)device:(ICDevice *)device didOpenSessionWithError:(NSError *)error {
    self.sessionError = error;
    self.opened = error == nil;
    self.openComplete = YES;
}

- (void)device:(ICDevice *)device didCloseSessionWithError:(NSError *)error {
    self.sessionError = error;
    self.opened = NO;
    self.closeComplete = YES;
}

- (void)didRemoveDevice:(ICDevice *)device {
    if (device == self.camera) self.removed = YES;
}

- (void)cameraDevice:(ICCameraDevice *)camera didAddItems:(NSArray<ICCameraItem *> *)items {}
- (void)cameraDevice:(ICCameraDevice *)camera didRemoveItems:(NSArray<ICCameraItem *> *)items {}
- (void)cameraDevice:(ICCameraDevice *)camera didRenameItems:(NSArray<ICCameraItem *> *)items {}
- (void)cameraDevice:(ICCameraDevice *)camera didReceiveThumbnail:(CGImageRef)thumbnail
                forItem:(ICCameraItem *)item error:(NSError *)error {}
- (void)cameraDevice:(ICCameraDevice *)camera didReceiveMetadata:(NSDictionary *)metadata
                forItem:(ICCameraItem *)item error:(NSError *)error {}
- (void)cameraDeviceDidChangeCapability:(ICCameraDevice *)camera {}
- (void)cameraDevice:(ICCameraDevice *)camera didReceivePTPEvent:(NSData *)eventData {}
- (void)deviceDidBecomeReadyWithCompleteContentCatalog:(ICCameraDevice *)device {}
- (void)cameraDeviceDidRemoveAccessRestriction:(ICDevice *)device {}
- (void)cameraDeviceDidEnableAccessRestriction:(ICDevice *)device {}
- (BOOL)cameraDevice:(ICCameraDevice *)camera shouldGetThumbnailOfItem:(ICCameraItem *)item { return NO; }
- (BOOL)cameraDevice:(ICCameraDevice *)camera shouldGetMetadataOfItem:(ICCameraItem *)item { return NO; }

- (void)startBrowser {
    self.browser = [ICDeviceBrowser new];
    self.browser.delegate = self;
    [self.browser start];
    double deadline = clockNow() + 8;
    while (!self.enumerated && clockNow() < deadline) pump(0.05);
    pump(0.2);
}

- (NSArray<NSDictionary *> *)deviceList {
    NSMutableArray *result = [NSMutableArray array];
    for (ICCameraDevice *device in self.cameras) {
        [result addObject:@{
            @"id": device.UUIDString ?: @"",
            @"name": device.name ?: @"",
            @"usb_vendor_id": @(device.usbVendorID),
            @"usb_product_id": @(device.usbProductID),
            @"has_open_session": @(device.hasOpenSession),
        }];
    }
    return result;
}

- (NSString *)openDevice:(NSString *)deviceID {
    if ([deviceID isEqualToString:@"auto"]) {
        if (self.cameras.count != 1) return @"connect exactly one Nikon camera or select --device from list-devices";
        self.camera = self.cameras.firstObject;
    } else {
        for (ICCameraDevice *device in self.cameras) {
            if ([device.UUIDString isEqualToString:deviceID]) self.camera = device;
        }
        if (!self.camera) return @"selected Nikon camera UUID was not found";
    }
    if (self.camera.hasOpenSession) return @"camera session is already open in another application";
    self.camera.delegate = self;
    [self.camera requestOpenSession];
    double deadline = clockNow() + 15;
    while (!self.openComplete && !self.removed && clockNow() < deadline) pump(0.05);
    if (self.removed) return @"camera disconnected while opening session";
    if (!self.openComplete) return @"camera session opening timed out";
    if (!self.opened) return self.sessionError.localizedDescription ?: @"camera session opening failed";
    return nil;
}

- (NSDictionary *)transact:(NSDictionary *)request {
    NSString *method = request[@"method"];
    NSNumber *operation = request[@"opcode"];
    NSArray<NSNumber *> *params = request[@"params"];
    if (![method isKindOfClass:NSString.class] || ![operation isKindOfClass:NSNumber.class] ||
        [operation longLongValue] < 0 || [operation unsignedLongLongValue] > UINT16_MAX ||
        !validNumbers(params) || !allowed(method, operation.unsignedShortValue, params, self.permitActivation)) {
        return failure(@"command rejected by second-generation PTP allowlist");
    }
    if (!self.opened || self.removed) return failure(@"camera session is closed or disconnected");
    if (self.pending || self.indeterminate) return failure(@"previous PTP command is unresolved; refusing an overlapping command");
    NSData *command = commandPacket(operation.unsignedShortValue, params, self.transaction++);
    __block BOOL completed = NO;
    __block NSData *replyData = nil;
    __block NSData *replyContainer = nil;
    __block NSError *replyError = nil;
    self.pending = YES;
    @try {
        [self.camera requestSendPTPCommand:command outData:nil completion:^(NSData *data, NSData *response, NSError *error) {
            dispatch_async(dispatch_get_main_queue(), ^{
                replyData = [data copy] ?: [NSData data];
                replyContainer = [response copy] ?: [NSData data];
                replyError = error;
                completed = YES;
                self.pending = NO;
            });
        }];
    } @catch (NSException *exception) {
        self.pending = NO;
        self.indeterminate = YES;
        return failure(exception.reason ?: @"ImageCaptureCore rejected the PTP command; outcome is uncertain");
    }
    double deadline = clockNow() + 30;
    while (!completed && !self.removed && clockNow() < deadline) pump(0.025);
    if (self.removed) return failure(@"camera disconnected during PTP command");
    if (!completed) {
        self.indeterminate = YES;
        return failure(@"PTP command timed out; result may be ambiguous; no further PTP commands sent");
    }
    if (replyError) return failure(replyError.localizedDescription);
    if (replyData.length > 4 * 1024 * 1024) return failure(@"PTP data exceeded the supported size");
    NSDictionary *parsed = parseResponse(replyContainer);
    if (parsed[@"error"]) return parsed;
    NSMutableDictionary *result = [parsed mutableCopy];
    result[@"data_base64"] = [replyData base64EncodedStringWithOptions:0];
    return result;
}

- (void)close {
    if (self.pending) {
        double pendingDeadline = clockNow() + 5;
        while (self.pending && clockNow() < pendingDeadline) pump(0.05);
    }
    if (self.opened && !self.removed && !self.pending) {
        self.closeComplete = NO;
        [self.camera requestCloseSession];
        double deadline = clockNow() + 5;
        while (!self.closeComplete && clockNow() < deadline) pump(0.05);
    }
    [self.browser stop];
}
@end

static int selfCheck(void) {
    NSData *packet = commandPacket(0x1001, @[], 1);
    const uint8_t expected[] = { 12, 0, 0, 0, 1, 0, 1, 16, 1, 0, 0, 0 };
    if (packet.length != sizeof(expected) || memcmp(packet.bytes, expected, sizeof(expected)) != 0) return 1;
    const uint8_t response[] = { 12, 0, 0, 0, 3, 0, 1, 32, 1, 0, 0, 0 };
    NSDictionary *parsed = parseResponse([NSData dataWithBytes:response length:sizeof(response)]);
    if (![parsed[@"code"] isEqual:@0x2001] || [parsed[@"params"] count] != 0) return 1;
    if (!allowed(@"read", 0x1001, @[], NO) ||
        !allowed(@"read", 0x94FF, @[@0xF02D, @0, @0x1FFFC, @0, @0], NO) ||
        !allowed(@"send", 0x944B, @[], NO) ||
        !allowed(@"send", 0x94FE, @[@0xF002, @0, @0, @0, @0], NO)) return 1;
    for (NSNumber *service in @[@0xF001, @0xC101, @0xC051]) {
        if (!allowed(@"send", 0x94FD, @[service, @0, @0, @0, @0], NO)) return 1;
    }
    NSArray<NSArray<NSNumber *> *> *activation = @[
        @[@0xC102, @0x28CD7EE8, @0, @0, @0],
        @[@0xC052, @1, @0, @0, @0],
        @[@0xF030, @0, @0, @0, @0],
    ];
    for (NSArray<NSNumber *> *params in activation) {
        if (!allowed(@"send", 0x94FD, params, YES) || allowed(@"send", 0x94FD, params, NO)) return 1;
    }
    if (allowed(@"send", 0x94FD, @[@0xF004, @0, @0, @0, @0], YES)) return 1;
    emitJSON(@{ @"self_check": @"passed" });
    return 0;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc == 2 && strcmp(argv[1], "--self-check") == 0) return selfCheck();
        BOOL list = argc == 2 && strcmp(argv[1], "--list") == 0;
        BOOL open = (argc == 3 || argc == 4) && strcmp(argv[1], "--open") == 0 &&
                    (argc == 3 || strcmp(argv[3], "--activate") == 0);
        if (!list && !open) {
            fprintf(stderr, "usage: mac_ptp_bridge --list | --open DEVICE_UUID [--activate] | --self-check\n");
            return 2;
        }
        NikonBridge *bridge = [NikonBridge new];
        bridge.permitActivation = open && argc == 4;
        [bridge startBrowser];
        if (list) {
            emitJSON(@{ @"devices": [bridge deviceList] });
            [bridge close];
            return 0;
        }
        NSString *deviceID = [NSString stringWithUTF8String:argv[2]];
        NSString *error = [bridge openDevice:deviceID];
        if (error) {
            emitJSON(failure(error));
            [bridge close];
            return 1;
        }
        emitJSON(@{ @"ready": @YES, @"device_id": bridge.camera.UUIDString ?: @"",
                    @"device_name": bridge.camera.name ?: @"" });
        char *line = NULL;
        size_t capacity = 0;
        while (YES) {
            struct pollfd source = { .fd = STDIN_FILENO, .events = POLLIN | POLLHUP, .revents = 0 };
            int count = poll(&source, 1, 50);
            pump(0.001);
            if (count < 0) {
                if (errno == EINTR) continue;
                break;
            }
            if (count == 0) continue;
            if (!(source.revents & (POLLIN | POLLHUP))) break;
            ssize_t size = getline(&line, &capacity, stdin);
            if (size < 0) break;
            NSData *input = [NSData dataWithBytes:line length:(NSUInteger)size];
            NSDictionary *request = [NSJSONSerialization JSONObjectWithData:input options:0 error:nil];
            if (![request isKindOfClass:NSDictionary.class]) {
                emitJSON(failure(@"invalid JSON request"));
                continue;
            }
            if ([request[@"method"] isEqual:@"close"]) {
                emitJSON(@{ @"closed": @YES });
                break;
            }
            emitJSON([bridge transact:request]);
        }
        free(line);
        [bridge close];
        return 0;
    }
}
