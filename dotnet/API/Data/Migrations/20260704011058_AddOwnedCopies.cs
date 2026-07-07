using System;
using Microsoft.EntityFrameworkCore.Migrations;

#nullable disable

namespace API.Data.Migrations
{
    /// <inheritdoc />
    public partial class AddOwnedCopies : Migration
    {
        /// <inheritdoc />
        protected override void Up(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropIndex(
                name: "IX_TrackedCards_UserName_Game_ProductId_Kind",
                table: "TrackedCards");

            migrationBuilder.AddColumn<DateTime>(
                name: "AcquiredAt",
                table: "TrackedCards",
                type: "TEXT",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "Grade",
                table: "TrackedCards",
                type: "TEXT",
                nullable: true);

            migrationBuilder.AddColumn<string>(
                name: "Note",
                table: "TrackedCards",
                type: "TEXT",
                nullable: true);

            migrationBuilder.AddColumn<double>(
                name: "PurchasePrice",
                table: "TrackedCards",
                type: "REAL",
                nullable: true);

            migrationBuilder.CreateIndex(
                name: "IX_TrackedCards_UserName_Game_Kind_ProductId",
                table: "TrackedCards",
                columns: new[] { "UserName", "Game", "Kind", "ProductId" });

            migrationBuilder.CreateIndex(
                name: "IX_TrackedCards_UserName_Game_ProductId",
                table: "TrackedCards",
                columns: new[] { "UserName", "Game", "ProductId" },
                unique: true,
                filter: "\"Kind\" = 'wishlist'");
        }

        /// <inheritdoc />
        protected override void Down(MigrationBuilder migrationBuilder)
        {
            migrationBuilder.DropIndex(
                name: "IX_TrackedCards_UserName_Game_Kind_ProductId",
                table: "TrackedCards");

            migrationBuilder.DropIndex(
                name: "IX_TrackedCards_UserName_Game_ProductId",
                table: "TrackedCards");

            migrationBuilder.DropColumn(
                name: "AcquiredAt",
                table: "TrackedCards");

            migrationBuilder.DropColumn(
                name: "Grade",
                table: "TrackedCards");

            migrationBuilder.DropColumn(
                name: "Note",
                table: "TrackedCards");

            migrationBuilder.DropColumn(
                name: "PurchasePrice",
                table: "TrackedCards");

            migrationBuilder.CreateIndex(
                name: "IX_TrackedCards_UserName_Game_ProductId_Kind",
                table: "TrackedCards",
                columns: new[] { "UserName", "Game", "ProductId", "Kind" },
                unique: true);
        }
    }
}
