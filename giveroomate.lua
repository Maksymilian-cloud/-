  local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Players = game:GetService("Players")

local targetPlayerName = "Blooded2025" 
local totalHouses = 37
local delay = 0.5 -- seconds

local function givePermissionToAllHouses()
    local targetPlayer = Players:FindFirstChild(targetPlayerName)
    if targetPlayer then
        for houseNumber = 1, totalHouses do
            local args = {
                [1] = "GivePermissionLoopToServer",
                [2] = targetPlayer,
                [3] = houseNumber
            }

            -- Fire the event to give permission to the specified player and house
            ReplicatedStorage:FindFirstChild("RE"):FindFirstChild("1Playe1rTrigge1rEven1t"):FireServer(unpack(args))
print("permission gave " .. houseNumber .. " " .. targetPlayerName .. "")
            -- Wait for the specified delay before giving permission to the next house
            wait(delay)
        end
    else
        warn("Player not found: " .. targetPlayerName)
    end
end

givePermissionToAllHouses()